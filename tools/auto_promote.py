"""Sinh test Playwright THẬT từ một finding discovery (Midscene) của một run.

    python tools/auto_promote.py --run r-0032 --finding f-sig-dom_unchanged
    python tools/auto_promote.py --run r-0032 --finding f-sig-dom_unchanged --out tests_generated --force

Nguồn dữ liệu: promote_candidate.repro_steps / .suggested_assertion trong
runs/<run_id>/results/<task_id>.json (đọc qua dashboard.runs_reader.find_finding — một chỗ parse
duy nhất, dùng chung với dashboard). repro_steps là tiếng Việt tự nhiên nên việc "dịch" sang code
dùng BẢNG TEMPLATE TẤT ĐỊNH (không LLM): test đã promote phải chạy lại y hệt mỗi lần. Bước nào
không khớp bảng thì sinh assertion fail tường minh — KHÔNG BAO GIỜ âm thầm cho pass.

[EXTERNAL GAP] Bảng template dưới đây chỉ phủ các thao tác của toyapp/static/index.html (#title,
#body, nút "Xoá"). Tổng quát hoá cho ứng dụng bất kỳ đòi hỏi LLM codegen có người review — course
không nói tới việc này.

Sinh CẢ HAI định dạng vì .venv của repo này KHÔNG có gói Python `playwright` (không được cài thêm —
xem CLAUDE.md), trong khi node_modules/playwright + Chromium (ms-playwright) đã có sẵn:
  - test_promoted_<slug>.py       -> pytest.importorskip, SKIP cho tới khi ai đó cài playwright python.
  - promoted_<slug>.spec.mjs      -> `node promoted_<slug>.spec.mjs`, CHẠY THẬT ngay bây giờ.

Ghi ra tests_generated/, nằm ngoài testpaths=tests (pytest.ini) nên không ảnh hưởng suite chính.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from dashboard import runs_reader  # noqa: E402

FINDING_ID_RE = re.compile(r"^[A-Za-z0-9_-]+$")  # chặn path traversal khi ghép vào tên file


def _classify_step(step: str) -> str:
    low = step.lower()
    if re.search(r"mở\s*/", low):
        return "goto"
    if "thêm" in low and "note" in low:
        return "add_note"
    if "xoá" in low or "xóa" in low:
        return "delete"
    return "unknown"


def _classify_assertion(text: str | None) -> str:
    low = (text or "").lower()
    if "không còn" in low and "danh sách" in low:
        return "gone"
    return "unknown"


def _plan_steps(repro_steps: list[str], assertion: str | None) -> tuple[list[tuple[str, str]], bool]:
    """Trả về [(kind, raw_step)] theo THỨ TỰ trong repro_steps + assertion cuối, và cờ ok (mọi thứ đều map được)."""
    plan = [(_classify_step(s), s) for s in repro_steps]
    plan.append((_classify_assertion(assertion), assertion or "(không có suggested_assertion)"))
    ok = all(kind != "unknown" for kind, _ in plan)
    return plan, ok


# --- Python (playwright.sync_api) ---------------------------------------------------------------

_PY_SNIPPETS = {
    "goto": '        page.goto(BASE + "/")',
    "add_note": (
        '        page.fill("#title", title)\n'
        '        page.fill("#body", "auto-promote")\n'
        "        page.click('button[type=submit]')\n"
        "        page.wait_for_selector(f\"#notes li:has-text('{title}')\")"
    ),
    "delete": "        page.click(f\"#notes li:has-text('{title}') >> text=Xoá\")",
    "gone": (
        "        page.wait_for_function(\n"
        "            \"t => ![...document.querySelectorAll('#notes li')]"
        ".some(li => li.textContent.includes(t))\",\n"
        "            arg=title, timeout=3000)"
    ),
}


def _py_body(plan: list[tuple[str, str]]) -> str:
    lines, seen = [], set()
    for kind, raw in plan:
        if kind in _PY_SNIPPETS and kind not in seen:
            lines.append(_PY_SNIPPETS[kind])
            seen.add(kind)
        elif kind not in _PY_SNIPPETS:
            escaped = raw.replace('"', "'")
            lines.append(f'        pytest.fail("Không map được repro_step/assertion: {escaped}")')
    return "\n".join(lines)


def build_python(finding: dict, meta: dict, slug: str, plan: list[tuple[str, str]]) -> str:
    header = (
        f'"""GENERATED bởi tools/auto_promote.py — REVIEW TRƯỚC KHI MERGE.\n\n'
        f'run_id: {meta["run_id"]}\n'
        f'finding_id: {finding["finding_id"]}\n'
        f'task_id: {finding["task_id"]}\n'
        f'detected_by: {finding["detected_by"]}\n'
        f'evidence_sha256: {meta["sha256"]}\n'
        f'generated_at: {meta["generated_at"]}\n\n'
        f'Nguồn: promote_candidate trong runs/{meta["run_id"]}/results/{finding["task_id"]}.json.\n'
        f'Bước không map được sẽ pytest.fail() tường minh thay vì âm thầm pass.\n\n'
        f'Chạy:  pytest tests_generated/test_promoted_{slug}.py -q\n'
        f'SKIP cho tới khi cài: pip install playwright && python -m playwright install chromium\n'
        f'(chưa cài trong .venv của repo này — dùng promoted_{slug}.spec.mjs để chạy thật bằng Node).\n'
        f'"""\n'
    )
    body = _py_body(plan)
    return (
        header
        + "from __future__ import annotations\n\n"
        + "import os\nimport uuid\n\nimport pytest\n\n"
        + 'pytest.importorskip("playwright.sync_api", reason="playwright (python) chưa cài trong .venv này")\n'
        + "from playwright.sync_api import sync_playwright  # noqa: E402\n\n"
        + 'BASE = os.environ.get("APP_BASE_URL", "http://127.0.0.1:8000")\n\n\n'
        + f"def test_promoted_{slug}():\n"
        + '    title = f"qc-{uuid.uuid4().hex[:8]}"\n'
        + "    with sync_playwright() as p:\n"
        + "        browser = p.chromium.launch()\n"
        + "        page = browser.new_page()\n"
        + body + "\n"
        + "        browser.close()\n"
    )


# --- JS (playwright, Node có sẵn trong node_modules) ---------------------------------------------

_JS_SNIPPETS = {
    "goto": "  await page.goto(BASE + '/');",
    "add_note": (
        "  await page.fill('#title', title);\n"
        "  await page.fill('#body', 'auto-promote');\n"
        "  await page.click('button[type=submit]');\n"
        "  await page.waitForSelector(`#notes li:has-text('${title}')`);"
    ),
    "delete": "  await page.click(`#notes li:has-text('${title}') >> text=Xoá`);",
    "gone": (
        "  await page.waitForFunction(\n"
        "    (t) => ![...document.querySelectorAll('#notes li')].some((li) => li.textContent.includes(t)),\n"
        "    title, { timeout: 3000 });"
    ),
}


def _js_body(plan: list[tuple[str, str]]) -> str:
    lines, seen = [], set()
    for kind, raw in plan:
        if kind in _JS_SNIPPETS and kind not in seen:
            lines.append(_JS_SNIPPETS[kind])
            seen.add(kind)
        elif kind not in _JS_SNIPPETS:
            escaped = raw.replace("'", "\\'")
            lines.append(f"  assert.fail('Không map được repro_step/assertion: {escaped}');")
    return "\n".join(lines)


def build_js(finding: dict, meta: dict, slug: str, plan: list[tuple[str, str]]) -> str:
    header = (
        f"// GENERATED bởi tools/auto_promote.py — REVIEW TRƯỚC KHI MERGE.\n"
        f"// run_id: {meta['run_id']}\n"
        f"// finding_id: {finding['finding_id']}\n"
        f"// task_id: {finding['task_id']}\n"
        f"// detected_by: {finding['detected_by']}\n"
        f"// evidence_sha256: {meta['sha256']}\n"
        f"// generated_at: {meta['generated_at']}\n"
        f"//\n"
        f"// Chạy:  node tests_generated/promoted_{slug}.spec.mjs   (playwright + Chromium đã cài sẵn)\n"
    )
    body = _js_body(plan)
    return (
        header
        + "\nimport { chromium } from 'playwright';\n"
        + "import assert from 'node:assert/strict';\n\n"
        + "const BASE = process.env.APP_BASE_URL || 'http://127.0.0.1:8000';\n\n"
        + "async function main() {\n"
        + "  const browser = await chromium.launch();\n"
        + "  const page = await browser.newPage();\n"
        + "  const title = 'qc-' + Math.random().toString(16).slice(2, 10);\n"
        + body + "\n"
        + "  await browser.close();\n"
        + "}\n\n"
        + "main().catch((e) => { console.error(e); process.exit(1); });\n"
    )


def promote(runs_dir: Path, report_dir: Path, out_dir: Path, run_id: str, finding_id: str,
            force: bool = False) -> dict:
    if not FINDING_ID_RE.match(finding_id):
        raise ValueError(f"finding_id không hợp lệ: {finding_id!r}")
    finding = runs_reader.find_finding(runs_dir, run_id, finding_id, report_dir)
    if finding is None:
        raise LookupError(f"không tìm thấy finding {finding_id!r} trong {run_id}")
    if not finding.get("repro_steps"):
        raise ValueError(f"finding {finding_id!r} không có repro_steps để promote")

    slug = re.sub(r"[^A-Za-z0-9_]", "_", finding_id)
    sha256 = next((e.get("sha256") for e in finding.get("evidence") or [] if e.get("sha256")), None)
    meta = {"run_id": run_id, "sha256": sha256 or "(không có evidence sha256)",
            "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S")}
    plan, mapped_ok = _plan_steps(finding["repro_steps"], finding.get("suggested_assertion"))

    out_dir.mkdir(parents=True, exist_ok=True)
    py_path = out_dir / f"test_promoted_{slug}.py"
    js_path = out_dir / f"promoted_{slug}.spec.mjs"
    exists = py_path.is_file() or js_path.is_file()
    if exists and not force:
        return {"ok": True, "exists": True, "mapped_ok": mapped_ok,
                "files": [str(py_path), str(js_path)]}

    py_path.write_text(build_python(finding, meta, slug, plan), encoding="utf-8")
    js_path.write_text(build_js(finding, meta, slug, plan), encoding="utf-8")
    return {"ok": True, "exists": False, "mapped_ok": mapped_ok,
            "files": [str(py_path), str(js_path)]}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--run", required=True, help="run_id, vd r-0032")
    ap.add_argument("--finding", required=True, help="finding_id, vd f-sig-dom_unchanged")
    ap.add_argument("--out", default=str(ROOT / "tests_generated"))
    ap.add_argument("--force", action="store_true", help="ghi đè file đã tồn tại")
    ap.add_argument("--runs-dir", default=str(ROOT / "runs"))
    ap.add_argument("--report-dir", default=str(ROOT / "midscene_run" / "report"))
    args = ap.parse_args()

    try:
        result = promote(Path(args.runs_dir), Path(args.report_dir), Path(args.out),
                          args.run, args.finding, force=args.force)
    except (ValueError, LookupError) as e:
        print(json.dumps({"ok": False, "error": str(e)}, ensure_ascii=False))
        return 2

    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
