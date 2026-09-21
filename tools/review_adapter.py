"""Checklist 5 câu của A cho một adapter (arch §3.2, plan-execution F-23).
    python tools/review_adapter.py --result runs/x/result.json --spec tests/fixtures/task_x.json --crash runs/x/crash.json
  --result : result của một lần chạy BÌNH THƯỜNG   --spec : task spec đã dùng để sinh ra nó
  --crash  : result của một lần chạy mà worker BỊ HỎNG có chủ đích (tắt toy app / gỡ key / script lỗi cú pháp)
Chạy từ thư mục gốc repo (uri của evidence là đường dẫn tương đối so với cwd, như core.evidence.collect).
Exit 0 = 5/5 · 1 = có câu FAIL · 2 = không đọc được file đầu vào.
"""
import argparse, json, re, sys
from pathlib import Path

for _s in (sys.stdout, sys.stderr):  # nhãn tiếng Việt: không được chết trên console cp1252 khi chưa nạp env.ps1
    if _s and hasattr(_s, "reconfigure"):
        _s.reconfigure(encoding="utf-8")

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from core import schema  # noqa: E402
from core.evidence import sha256_file  # noqa: E402


def load(path):
    return json.loads(Path(path).read_text(encoding="utf-8-sig"))


def c1_schema(res):
    e = schema.validate_result(res)
    return not e, "; ".join(e[:3])


def c2_crash_is_error(crash):
    e = schema.validate_result(crash)
    ok = isinstance(crash, dict) and crash.get("status") == "error" and not e
    why = f"status={crash.get('status') if isinstance(crash, dict) else type(crash).__name__}"
    return ok, why + ("; " + "; ".join(e[:3]) if e else "")


def c3_evidence(res):
    ev = res.get("evidence") if isinstance(res, dict) else None
    if not ev:
        return False, "result không có evidence nào để kiểm"
    bad = []
    for item in ev:
        uri, want = (item.get("uri"), item.get("sha256")) if isinstance(item, dict) else (None, None)
        if not uri or not Path(uri).is_file():
            bad.append(f"{uri}: không tồn tại")
        elif sha256_file(uri) != want:
            bad.append(f"{uri}: sha256 lệch")
    return not bad, f"lệch: {bad[:3]}"


def c4_against_spec(spec, res):
    try:
        v = schema.check_result_against_spec(spec, res)
    except (KeyError, TypeError, AttributeError) as ex:  # spec/result sai cấu trúc -> không kiểm được -> FAIL
        return False, f"không kiểm được, cấu trúc sai: {type(ex).__name__}: {ex}"
    return not v, "; ".join(v[:3])


def worker_names():
    import yaml
    names = []
    for f in sorted((ROOT / "workers").glob("*.yaml")):
        if f.name.startswith("_"):
            continue
        doc = yaml.safe_load(f.read_text(encoding="utf-8-sig"))
        if isinstance(doc, dict) and doc.get("name"):
            names.append(str(doc["name"]))
    return names


def c5_no_worker_names_in_core():
    names = worker_names()
    if not names:  # không có tên để dò thì không được PASS "rỗng"
        return False, "không đọc được tên worker nào từ workers/*.yaml — không kiểm được"
    hits = [f"{p.name}:{n}" for p in sorted((ROOT / "core").glob("*.py")) for n in names
            if re.search(rf"\b{re.escape(n)}\b", p.read_text(encoding="utf-8-sig"), re.I)]
    return not hits, f"thấy: {hits[:3]}"


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--result", required=True)
    ap.add_argument("--spec", required=True)
    ap.add_argument("--crash", required=True)
    a = ap.parse_args()
    try:
        res, spec, crash = load(a.result), load(a.spec), load(a.crash)
    except (OSError, ValueError) as ex:
        print(f"ERROR không đọc được file đầu vào: {ex}", file=sys.stderr)
        return 2

    rows = [
        ("1. validate qua ĐÚNG result.json, không trường riêng", *c1_schema(res)),
        ("2. worker hỏng -> status=error (không phải fail/pass)", *c2_crash_is_error(crash)),
        ("3. mọi evidence tồn tại và sha256 khớp file", *c3_evidence(res)),
        ("4. verdict_source / gating đúng luật allOf + expected_result_kind", *c4_against_spec(spec, res)),
        ("5. không có tên worker nào trong core/", *c5_no_worker_names_in_core()),
    ]
    for label, ok, why in rows:
        print(("PASS " if ok else "FAIL ") + label + ("" if ok else "   <- " + why))
    return 0 if all(ok for _, ok, _ in rows) else 1


if __name__ == "__main__":
    sys.exit(main())
