"""Tiêu chí PoC #4 (F-11). So HAI run theo phần TẤT ĐỊNH. Dùng:  python tools/diff_runs.py runs/r-0001 runs/r-0002
Exit 0 = giống hệt · 1 = KHÁC · 2 = không so được (run_signature khác nhau, hoặc không đọc được report.json).
"""
import json, pathlib, sys

for _s in (sys.stdout, sys.stderr):  # nhãn tiếng Việt: không được chết trên console cp1252
    if _s and hasattr(_s, "reconfigure"):
        _s.reconfigure(encoding="utf-8")

KEYS = ("run_signature", "gate_verdict", "exit_code", "deterministic_view")


def load(run_dir):
    report = json.loads(pathlib.Path(run_dir, "report.json").read_text(encoding="utf-8-sig"))
    missing = [k for k in KEYS if not isinstance(report, dict) or k not in report]
    if missing:
        raise ValueError(f"thiếu trường {missing}")
    return report


def diff_view(va, vb):
    """Liệt kê chênh lệch giữa hai deterministic_view, ghép theo task_id (không theo vị trí)."""
    ia = {t.get("task_id", f"#{i}"): t for i, t in enumerate(va)}
    ib = {t.get("task_id", f"#{i}"): t for i, t in enumerate(vb)}
    lines = []
    for tid in ia.keys() | ib.keys():
        ta, tb = ia.get(tid), ib.get(tid)
        if ta == tb:
            continue
        if ta is None or tb is None:
            lines.append(f"  {tid}: chỉ có ở run {'B' if ta is None else 'A'}")
        lines.append(f"  - {ta}\n  + {tb}")
    return lines


def main(a, b):
    try:
        ra, rb = load(a), load(b)
    except (OSError, ValueError) as ex:
        print(f"KHÔNG SO ĐƯỢC: không đọc được report.json: {ex}", file=sys.stderr)
        return 2

    if ra["run_signature"] != rb["run_signature"]:
        print("KHÔNG SO ĐƯỢC: run_signature khác nhau (plan/SUT/worker/adapter/seed đổi).")
        print(" ", ra["run_signature"], "\n ", rb["run_signature"])
        return 2

    va = {k: ra[k] for k in KEYS}
    vb = {k: rb[k] for k in KEYS}
    if va == vb:
        print(f"IDENTICAL — {len(va['deterministic_view'])} kết quả gating, gate_verdict={va['gate_verdict']}")
        return 0

    print("DIFFERENT — đây là BUG CỦA HỆ THỐNG (cùng signature, khác verdict tất định):")
    for key in ("gate_verdict", "exit_code"):
        if va[key] != vb[key]:
            print(f"  {key}: {va[key]} -> {vb[key]}")
    for line in diff_view(va["deterministic_view"], vb["deterministic_view"]):
        print(line)
    return 1


if __name__ == "__main__":
    if len(sys.argv) != 3:
        print(__doc__, file=sys.stderr)
        sys.exit(2)
    sys.exit(main(sys.argv[1], sys.argv[2]))
