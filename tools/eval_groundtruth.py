"""Đo "đạt 90%" của Sprint 1 (S1-08): bộ Ground-Truth có phủ AC không, TC sinh ra có chạy xanh trên SUT sạch không, và bộ ĐÃ DUYỆT có bắt được lỗi nghiệp vụ không.

    python tools/eval_groundtruth.py --prd tests/fixtures/prd/noteboard-prd.md --golden tests/fixtures/prd/noteboard-golden.yaml \\
        --sut-root tests/fixtures/sut/noteboard --llm fake --out-json out.json
    python tools/eval_groundtruth.py ... --llm real --runs 3 --yes        # TỐN TIỀN: cần khoá LLM + --yes, in ước tính chi phí trước; chỉ chạy khi đã được đồng ý

Ba metric (ngưỡng đặt ở THRESHOLDS; exit 0 khi đạt cả bốn điều kiện, 1 khi không đạt, 3 khi sai cách dùng/lỗi hệ thống):
  (a) AC coverage   = AC `testable` (theo golden) có >= 1 TC / tổng AC `testable`.          (mỗi lượt sinh; --runs N lấy median)
  (b) TC xanh       = TC vừa sinh chạy pass trên toyapp SẠCH (QC_BUGS=none) / tổng TC sinh ra. TC được ép `approved` trong bản sao tạm, không đụng file thật.
  (c) Bắt mutant    = bộ ĐÃ DUYỆT (`<sut-root>/.qc-agent/ground-truth`) fail khi toyapp bật QC_BUGS=<k>, k = 4…13 (>= 9/10) và riêng BUG-1 (bắt buộc).
Toyapp chạy bằng subprocess uvicorn trên cổng trống; dùng "none" chứ không dùng chuỗi rỗng (PowerShell coi biến môi trường rỗng là xoá biến).
Đo (c) chỉ có nghĩa khi bộ đã duyệt XANH trên SUT sạch: không thì báo `baseline_green: false` và exit 1.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import socket
import statistics
import subprocess
import sys
import tempfile
import time
import xml.etree.ElementTree as ET
from pathlib import Path

import httpx
import yaml

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
from qc_agent.llm.prices import PRICES  # noqa: E402  (bảng giá duy nhất)

THRESHOLDS = {"ac_coverage": 0.9, "green_rate": 0.9, "mutants_killed_min": 9, "mutants_total": 10}
MUTANTS = tuple(f"BUG-{n}" for n in range(4, 14))
BUG1 = "BUG-1"
DEFAULT_FAKE = ROOT / "tests" / "fixtures" / "llm" / "gt_noteboard_response.json"
DEFAULT_OPENAPI = ROOT / "tests" / "fixtures" / "openapi" / "noteboard.json"
PRICES_PER_MTOK = {model: (price[0], price[1]) for model, price in PRICES.items()}   # (vào, ra) USD/MTok, SUY RA từ llm/prices.py (bảng duy nhất); model khác thì truyền --price-in/--price-out
GT_DIR = ".qc-agent/ground-truth"
_TC_NAME = re.compile(r"\[(TC-[^\]]+)\]")


class EvalError(RuntimeError):
    """Sai cách dùng hoặc lỗi hệ thống (exit 3). Thông điệp không chứa nội dung PRD hay khoá."""


# ---------------- phần thuần (có unit test) ----------------

def load_golden(path: Path) -> dict:
    data = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    if not isinstance(data, dict) or not isinstance(data.get("acs"), dict) or not isinstance(data.get("mutants"), dict):
        raise EvalError("golden sai cấu trúc (cần `acs` và `mutants`)")
    return data


def testable_acs(golden: dict) -> list[str]:
    return sorted(ac for ac, spec in golden["acs"].items() if isinstance(spec, dict) and spec.get("testable") is True)


def ac_coverage(golden: dict, test_cases: list[dict]) -> dict:
    """AC `testable` có >= 1 TC. TC `rejected` không tính (loại rồi thì không phải là phủ)."""
    testable = testable_acs(golden)
    covered = {ref for tc in test_cases if tc.get("status") != "rejected" for ref in tc.get("ac_refs", [])}
    missing = [ac for ac in testable if ac not in covered]
    return {"testable": len(testable), "covered": len(testable) - len(missing), "missing": missing,
            "value": (len(testable) - len(missing)) / len(testable) if testable else 0.0}


def parse_junit(xml_text: str) -> dict[str, str]:
    """{tc_id: passed|failed|error|skipped} từ JUnit của pytest (test_story[TC-…]). Test không có tc_id trong tên giữ nguyên tên."""
    outcomes: dict[str, str] = {}
    for case in ET.fromstring(xml_text).iter("testcase"):
        found = _TC_NAME.search(case.get("name", ""))
        key = found.group(1) if found else case.get("name", "?")
        outcomes[key] = ("failed" if case.find("failure") is not None else "error" if case.find("error") is not None
                         else "skipped" if case.find("skipped") is not None else "passed")
    return outcomes


def green_rate(outcomes: dict[str, str]) -> dict:
    total = len(outcomes)
    passed = sum(1 for state in outcomes.values() if state == "passed")
    return {"total": total, "passed": passed, "failing": sorted(k for k, s in outcomes.items() if s != "passed"), "value": passed / total if total else 0.0}


def mutant_result(bug: str, returncode: int, outcomes: dict[str, str], golden: dict, catalog_tcs: list[dict]) -> dict:
    """Một mutant bị BẮT khi bộ đã duyệt fail (pytest exit 1). exit 0 = sống sót; mã khác (2,3,4,5) là lỗi đo: không tính là bắt."""
    failing = sorted(k for k, s in outcomes.items() if s in ("failed", "error"))
    expected = set((golden["mutants"].get(bug) or {}).get("acs", []))
    by_expected_ac = [tc["tc_id"] for tc in catalog_tcs if tc["tc_id"] in failing and expected & set(tc.get("ac_refs", []))]
    return {"killed": returncode == 1 and bool(failing), "exit_code": returncode, "failing_tcs": failing, "caught_by_expected_ac": bool(by_expected_ac),
            "measurement_error": returncode not in (0, 1)}


def summarize_mutants(results: dict[str, dict]) -> dict:
    killed = [bug for bug in MUTANTS if results.get(bug, {}).get("killed")]
    return {"killed": killed, "survived": [bug for bug in MUTANTS if bug not in killed], "count": len(killed), "total": len(MUTANTS),
            "value": len(killed) / len(MUTANTS), "bug1_killed": bool(results.get(BUG1, {}).get("killed"))}


def verdict(coverage: float, green: float, mutants: dict, baseline_green: bool) -> dict:
    checks = {
        "ac_coverage": coverage >= THRESHOLDS["ac_coverage"],
        "green_rate": green >= THRESHOLDS["green_rate"],
        "mutants": mutants["count"] >= THRESHOLDS["mutants_killed_min"],
        "bug1": bool(mutants["bug1_killed"]),
        "baseline_green": baseline_green,
    }
    return {"checks": checks, "passed": all(checks.values())}


def median(values: list[float]) -> float:
    return statistics.median(values) if values else 0.0


def estimate_cost(input_tokens: int, output_tokens: int, runs: int, price_in: float, price_out: float) -> dict:
    per_run = input_tokens * price_in / 1e6 + output_tokens * price_out / 1e6
    return {"input_tokens_per_run": input_tokens, "output_tokens_per_run_max": output_tokens, "runs": runs, "usd_per_run": round(per_run, 4), "usd_total": round(per_run * runs, 4)}


def render_markdown(report: dict) -> str:
    cov, green, mut, verdict_ = report["ac_coverage"], report["green_rate"], report["mutants"], report["verdict"]
    mark = lambda ok: "✅" if ok else "❌"  # noqa: E731
    lines = [f"## Đo Ground-Truth — `{report['prd_id']}` · llm={report['llm']} · model={report['model']} · {len(report['runs'])} lượt", "",
             "| Metric | Giá trị | Ngưỡng | |", "|---|---|---|---|",
             f"| (a) AC coverage (median) | {cov['median']:.1%} ({cov['covered']}/{cov['testable']} AC testable ở lượt thấp nhất) | ≥ {THRESHOLDS['ac_coverage']:.0%} | {mark(verdict_['checks']['ac_coverage'])} |",
             f"| (b) TC chạy xanh trên SUT sạch (median) | {green['median']:.1%} | ≥ {THRESHOLDS['green_rate']:.0%} | {mark(verdict_['checks']['green_rate'])} |",
             f"| (c) Mutant bị bắt (bộ đã duyệt) | {mut['count']}/{mut['total']} | ≥ {THRESHOLDS['mutants_killed_min']}/{mut['total']} | {mark(verdict_['checks']['mutants'])} |",
             f"| (c) BUG-1 bị bắt | {'có' if mut['bug1_killed'] else 'không'} | bắt buộc | {mark(verdict_['checks']['bug1'])} |",
             f"| Bộ đã duyệt xanh trên SUT sạch | {'có' if report['baseline_green'] else 'không'} | bắt buộc | {mark(verdict_['checks']['baseline_green'])} |", "",
             "| Mutant | Bắt? | AC theo golden | TC fail |", "|---|---|---|---|"]
    for bug in (*MUTANTS, BUG1):
        result = report["mutant_results"].get(bug, {})
        acs = ", ".join((report["golden_mutants"].get(bug) or {}).get("acs", [])) or "—"
        failing = ", ".join(result.get("failing_tcs", [])[:3]) + (" …" if len(result.get("failing_tcs", [])) > 3 else "")
        lines.append(f"| {bug} | {mark(result.get('killed', False))}{' (đo lỗi)' if result.get('measurement_error') else ''} | {acs} | {failing or '—'} |")
    if mut["survived"]:
        lines += ["", f"Mutant sống sót: {', '.join(mut['survived'])}"]
    if cov["missing"]:
        lines += ["", f"AC testable chưa có TC (lượt thấp nhất): {', '.join(cov['missing'])}"]
    lines += ["", f"**Kết luận: {'ĐẠT' if verdict_['passed'] else 'KHÔNG ĐẠT'}**"]
    return "\n".join(lines) + "\n"


# ---------------- chạy SUT và pytest ----------------

def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


class Toyapp:
    """uvicorn của SUT trên cổng trống với QC_BUGS cho trước (dùng "none" cho sạch)."""

    def __init__(self, sut_root: Path, bugs: str, app: str = "toyapp.app:app"):
        self.sut_root, self.bugs, self.app = Path(sut_root), bugs, app

    def __enter__(self) -> str:
        port = free_port()
        self.proc = subprocess.Popen([sys.executable, "-m", "uvicorn", "--app-dir", str(self.sut_root), self.app, "--host", "127.0.0.1", "--port", str(port)],
                                     env={**os.environ, "QC_BUGS": self.bugs}, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        url = f"http://127.0.0.1:{port}"
        for _ in range(120):
            if self.proc.poll() is not None:
                break
            try:
                httpx.get(f"{url}/__qc/config", timeout=1)
                return url
            except httpx.HTTPError:
                time.sleep(0.25)
        self.proc.terminate()
        raise EvalError(f"toyapp không lên (QC_BUGS={self.bugs})")

    def __exit__(self, *exc):
        self.proc.terminate()
        try:
            self.proc.wait(timeout=20)
        except subprocess.TimeoutExpired:
            self.proc.kill()


def run_suite(workspace: Path, base_url: str) -> tuple[int, dict[str, str]]:
    """pytest trên `<workspace>/.qc-agent/ground-truth/tests_gt` (cwd = workspace: rootdir là pytest.ini của thư mục test, không đọc cấu hình nào khác)."""
    junit = workspace / "junit.xml"
    junit.unlink(missing_ok=True)
    env = {**os.environ, "APP_BASE_URL": base_url, "PYTEST_DISABLE_PLUGIN_AUTOLOAD": "1", "PYTEST_ADDOPTS": ""}
    done = subprocess.run([sys.executable, "-m", "pytest", f"{GT_DIR}/tests_gt", "-q", "-p", "no:cacheprovider", "--junitxml", str(junit)],
                          cwd=workspace, env=env, capture_output=True, text=True, encoding="utf-8", timeout=600)
    outcomes = parse_junit(junit.read_text(encoding="utf-8")) if junit.exists() else {}
    return done.returncode, outcomes


def measure_mutants(sut_root: Path, golden: dict, app: str) -> tuple[bool, dict[str, dict], list[dict]]:
    source = Path(sut_root) / GT_DIR
    if not (source / "test-cases.yaml").is_file():
        raise EvalError(f"không có bộ Ground-Truth đã duyệt ở {source}")
    catalog = yaml.safe_load((source / "test-cases.yaml").read_text(encoding="utf-8"))
    approved = [tc for tc in catalog["test_cases"] if tc.get("status") == "approved"]
    with tempfile.TemporaryDirectory() as tmp:
        workspace = Path(tmp)
        shutil.copytree(source, workspace / GT_DIR, ignore=shutil.ignore_patterns("__pycache__", ".pytest_cache"))
        with Toyapp(sut_root, "none", app) as url:
            code, outcomes = run_suite(workspace, url)
        baseline_green = code == 0 and bool(outcomes) and all(state == "passed" for state in outcomes.values())
        results: dict[str, dict] = {}
        for bug in (*MUTANTS, BUG1):
            with Toyapp(sut_root, bug.split("-")[1], app) as url:
                code, outcomes = run_suite(workspace, url)
            results[bug] = mutant_result(bug, code, outcomes, golden, approved)
    return baseline_green, results, approved


# ---------------- sinh (fake/real) ----------------

def _generate_once(prd_path: Path, openapi_source: str | None, llm: str, fake_response: Path, model: str, egress_dir: Path):
    import httpx as _httpx
    from qc_agent.groundtruth.generate import generate
    from qc_agent.groundtruth.prd import parse_prd
    prd = parse_prd(prd_path, openapi_source=openapi_source)
    if llm == "fake":
        payload = json.loads(Path(fake_response).read_text(encoding="utf-8"))
        transport = _httpx.MockTransport(lambda request: _httpx.Response(200, json=payload))
        return prd, generate(prd, model=model, egress_dir=egress_dir, transport=transport)
    return prd, generate(prd, model=model, egress_dir=egress_dir)


def measure_generation(catalog: dict, golden: dict, sut_root: Path, analysis, app: str) -> dict:
    """(a) và (b) cho MỘT lượt sinh."""
    from qc_agent.groundtruth import render as gt_render
    coverage = ac_coverage(golden, catalog["test_cases"])
    forced = json.loads(json.dumps(catalog))
    for tc in forced["test_cases"]:
        tc["status"] = "approved"
    forced["status"] = "approved"
    with tempfile.TemporaryDirectory() as tmp:
        workspace = Path(tmp)
        gt_render.write(gt_render.render(forced, sut_root=workspace, openapi=analysis), workspace)
        with Toyapp(sut_root, "none", app) as url:
            code, outcomes = run_suite(workspace, url)
    greens = green_rate(outcomes)
    return {"ac_coverage": coverage, "green_rate": greens, "test_cases": len(catalog["test_cases"]), "exit_code": code}


def estimate_for(prd_path: Path, openapi_source: str | None, runs: int, model: str, price_in: float | None, price_out: float | None) -> dict:
    from qc_agent.groundtruth import generate as gen
    from qc_agent.groundtruth.prd import parse_prd
    prd = parse_prd(prd_path, openapi_source=openapi_source)
    _, system = gen.load_prompt()
    chars = len(system) + len(gen.build_user(prd)) + len(json.dumps(gen.tool_schema()))
    known = PRICES_PER_MTOK.get(model)
    if price_in is None or price_out is None:
        if known is None:
            raise EvalError(f"chưa biết giá của model {model!r}: truyền --price-in và --price-out (USD/MTok)")
        price_in, price_out = known
    return estimate_cost(int(chars / 3), gen.MAX_TOKENS, runs, price_in, price_out)


def main(argv: list[str]) -> int:
    os.environ["QC_GT_CACHE_DIR"] = "none"   # đo model: cache sẽ làm các lượt lặp trả cùng một kết quả và sai số liệu/chi phí (S4-02)
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--prd", required=True, type=Path)
    ap.add_argument("--golden", required=True, type=Path)
    ap.add_argument("--sut-root", required=True, type=Path, help="SUT có bộ GT đã duyệt ở .qc-agent/ground-truth và toyapp để chạy")
    ap.add_argument("--llm", choices=("fake", "real"), default="fake")
    ap.add_argument("--runs", type=int, default=1, help="số lượt sinh (median cho (a) và (b)); real nên dùng 3")
    ap.add_argument("--openapi", default=str(DEFAULT_OPENAPI) if DEFAULT_OPENAPI.is_file() else None, help="OpenAPI của SUT (danh sách endpoint cho LLM)")
    ap.add_argument("--fake-response", type=Path, default=DEFAULT_FAKE, help="response Messages API phát lại ở chế độ fake")
    ap.add_argument("--app", default="toyapp.app:app", help="ứng dụng ASGI của SUT")
    ap.add_argument("--out-json", type=Path)
    ap.add_argument("--yes", action="store_true", help="xác nhận chi tiền/gửi PRD ra nhà cung cấp LLM (bắt buộc với --llm real)")
    ap.add_argument("--price-in", type=float, help="USD/MTok đầu vào (model chưa có trong bảng giá)")
    ap.add_argument("--price-out", type=float, help="USD/MTok đầu ra")
    try:
        args = ap.parse_args(argv)
    except SystemExit as exit_:
        return exit_.code if isinstance(exit_.code, int) and exit_.code == 0 else 3
    try:
        if not 1 <= args.runs <= 10:
            raise EvalError("--runs phải trong 1..10")
        from qc_agent import settings
        from qc_agent.scaffold import openapi as scaffold_openapi
        model = settings.get().gt_model
        golden = load_golden(args.golden)
        if args.llm == "real":
            estimate = estimate_for(args.prd, args.openapi, args.runs, model, args.price_in, args.price_out)
            print(f"ƯỚC TÍNH CHI PHÍ ({model}): ~{estimate['input_tokens_per_run']} token vào + tối đa {estimate['output_tokens_per_run_max']} token ra mỗi lượt, "
                  f"{estimate['runs']} lượt ≈ tối đa ${estimate['usd_total']:.2f}. PRD được gửi tới nhà cung cấp LLM (ghi egress trước khi gửi).", file=sys.stderr)
            if not args.yes:
                raise EvalError("--llm real cần --yes (xác nhận chi tiền và việc gửi PRD ra ngoài)")
        else:
            from qc_agent.llm.client import provider_of
            if provider_of(model) == "gemini":
                model = "claude-sonnet-5"   # response giả có dạng Messages API: đừng để QC_GT_MODEL=gemini-* trong .env làm hỏng lượt fake
            if not os.environ.get("ANTHROPIC_API_KEY", "").strip():
                os.environ["ANTHROPIC_API_KEY"] = "sk-ant-eval-fake"   # khoá rỗng (vd. từ .env) cũng phải thay; client đòi có khoá dù transport là giả; không có request thật nào
        analysis = scaffold_openapi.analyze(scaffold_openapi.load(args.openapi)) if args.openapi else None
        runs = []
        with tempfile.TemporaryDirectory() as egress:
            for _ in range(args.runs):
                prd, generated = _generate_once(args.prd, args.openapi, args.llm, args.fake_response, model, Path(egress))
                measured = measure_generation(generated.catalog, golden, args.sut_root, analysis, args.app)
                measured["usage"] = {"input_tokens": generated.usage.input_tokens, "output_tokens": generated.usage.output_tokens}
                measured["orphans"] = list(generated.orphans)
                runs.append(measured)
        baseline_green, mutant_results, approved = measure_mutants(args.sut_root, golden, args.app)
    except EvalError as error:
        print(f"LỖI: {error}", file=sys.stderr)
        return 3
    except Exception as error:  # noqa: BLE001 — chỉ in loại lỗi: thông điệp của LLM/YAML có thể trích lại nội dung
        print(f"LỖI HỆ THỐNG: {type(error).__name__}", file=sys.stderr)
        return 3

    worst = min(runs, key=lambda r: r["ac_coverage"]["value"])
    mutants = summarize_mutants(mutant_results)
    coverage_median, green_median = median([r["ac_coverage"]["value"] for r in runs]), median([r["green_rate"]["value"] for r in runs])
    report = {
        "prd_id": prd.prd_id, "llm": args.llm, "model": model if args.llm == "real" else f"{model} (fake)", "golden_labeled_by": golden.get("labeled_by"),
        "runs": runs, "ac_coverage": {"median": coverage_median, **{k: worst["ac_coverage"][k] for k in ("testable", "covered", "missing")}},
        "green_rate": {"median": green_median}, "baseline_green": baseline_green, "approved_test_cases": len(approved),
        "mutant_results": mutant_results, "mutants": mutants, "golden_mutants": golden["mutants"], "thresholds": THRESHOLDS,
        "verdict": verdict(coverage_median, green_median, mutants, baseline_green),
    }
    if args.out_json:
        args.out_json.parent.mkdir(parents=True, exist_ok=True)
        args.out_json.write_text(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8", newline="\n")
    sys.stdout.write(render_markdown(report))
    if golden.get("labeled_by", "").endswith("CẦN QA DUYỆT"):
        print(f"Lưu ý: nhãn golden do `{golden['labeled_by']}`; số đo chỉ chính thức sau khi QA duyệt nhãn.", file=sys.stderr)
    return 0 if report["verdict"]["passed"] else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
