"""Đo token của request Diff Agent: diff thô so với diff đã prune. Chỉ offline (`estimate_input_tokens` = ceil(byte UTF-8 / 3), ước lượng gần đúng, không phải số đếm thật).

Bản hiện tại chỉ có `--ceiling` (S4-04 mục 0): TRẦN khả thi của mức giảm trên TOÀN BỘ request, trước khi tinh chỉnh pruner.
  A      request với `git diff` thô trong khung `<untrusted_diff>`
  P      tiền tố (tools + system) và khung `user` với danh sách file rỗng
  B_min  danh sách file ĐẦY ĐỦ nhưng không hunk nào: payload nhỏ nhất hợp lệ theo rào chắn "danh sách file luôn đầy đủ", nên mọi pruner hợp lệ có B >= B_min
  B0     payload của pruner hiện tại
Trần chặt = 1 - B_min/A (dùng để quyết định), trần lỏng = 1 - P/A, đã đạt = 1 - B0/A. Ca FULL SET, chỉ-floor và `token_cap` không gọi LLM nên không vào median.
"""
from __future__ import annotations

import argparse
import json
import math
import statistics
import subprocess
import sys
import tempfile
from dataclasses import asdict, replace
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "src"))
from qc_agent import settings  # noqa: E402
from qc_agent.core import project as project_lib, registry  # noqa: E402
from qc_agent.selector import agent, pruner, rules  # noqa: E402
from tools import eval_selector  # noqa: E402

TARGET = 0.40                                  # DoD S4: giảm >= 40% ở median
REAL_BASELINE = ROOT / "eval" / "selector-real.json"
EVIDENCE = "estimate"


def percentile(values: list[float], q: float) -> float:
    """Nearest-rank: phần tử thứ ceil(q*n) của dãy đã sắp. Không nội suy nên P90 của dãy nhỏ là một giá trị có thật."""
    if not values:
        raise ValueError("dãy rỗng")
    ordered = sorted(values)
    return ordered[max(0, math.ceil(q * len(ordered)) - 1)]


def reduction(smaller: int, larger: int) -> float:
    """Tỉ lệ giảm `1 - smaller/larger`; âm khi payload `smaller` thật ra lớn hơn."""
    return 1 - smaller / larger if larger else 0.0


def _raw_diff(checkout: Path, merge_base: str, head: str) -> str:
    # mặc định của git (-U3, không -w); --no-color/--no-ext-diff chỉ để đầu ra tất định
    run = subprocess.run(["git", "-c", "core.quotepath=off", "diff", "--no-color", "--no-ext-diff", merge_base, head], cwd=checkout, capture_output=True, check=True, timeout=30)
    return run.stdout.decode("utf-8", "replace")


def _minimal_payload(diff) -> str:
    """Danh sách file đầy đủ, không hunk. `truncated`/`dropped_hunks` về giá trị ngắn nhất để là cận dưới thật sự."""
    return json.dumps([asdict(replace(item, hunks=None, truncated=False, dropped_hunks=0)) for item in diff.files], ensure_ascii=False, sort_keys=True)


def measure_case(name: str, checkout: Path, base: str, head: str, request: agent.DiffRequest, policy: dict, suite_map: dict, module_map: dict | None, cap: int) -> dict:
    diff = pruner.prune(checkout, base, head)
    decision = rules.decide([rules.ChangedFile(item.path, item.status) for item in diff.files], policy, module_map, suite_map)
    users = {"A": agent.user_message(_raw_diff(checkout, diff.merge_base, head)), "P": agent.user_message("[]"),
             "B_min": agent.user_message(_minimal_payload(diff)), "B0": agent.user_message(agent.pruned_payload(diff))}
    tokens = {key: agent.estimate_request(request, user) for key, user in users.items()}
    route = "full_set" if decision.full_set else "floor_only" if decision.floor_only else "llm"
    capped = [key for key in ("A", "B0") if tokens[key] > cap]   # chặn ở A hay ở B0 (S4-04 mục 3 sẽ báo tách)
    return {"name": name, "route": route, "token_cap_at": capped, "files": len(diff.files), "tokens": tokens,
            "truncated": sum(item.truncated for item in diff.files), "dropped_hunks": sum(item.dropped_hunks for item in diff.files)}


def eligible(case: dict) -> bool:
    return case["route"] == "llm" and not case["token_cap_at"]


def summarize(cases: list[dict]) -> dict:
    """Chỉ ca đi đường LLM và không dính `token_cap` vào median/P90; các ca còn lại có dòng đếm riêng."""
    used = [case for case in cases if eligible(case)]
    excluded = {"full_set": sum(c["route"] == "full_set" for c in cases), "floor_only": sum(c["route"] == "floor_only" for c in cases),
                "token_cap": sum(c["route"] == "llm" and bool(c["token_cap_at"]) for c in cases)}
    out = {"cases": len(cases), "llm_cases": len(used), "excluded": excluded}
    if not used:
        return {**out, "passed": False, "reason": "không có ca nào đi đường LLM"}
    series = {"loose": [reduction(c["tokens"]["P"], c["tokens"]["A"]) for c in used],
              "tight": [reduction(c["tokens"]["B_min"], c["tokens"]["A"]) for c in used],
              "achieved": [reduction(c["tokens"]["B0"], c["tokens"]["A"]) for c in used]}
    for key, values in series.items():
        out[key] = {"median": statistics.median(values), "p90": percentile(values, .9)}
    out["median_tokens"] = {key: statistics.median(c["tokens"][key] for c in used) for key in ("A", "P", "B_min", "B0")}
    out["b_min_ge_a"] = [c["name"] for c in used if c["tokens"]["B_min"] >= c["tokens"]["A"]]
    out["b0_gt_a"] = [c["name"] for c in used if c["tokens"]["B0"] > c["tokens"]["A"]]
    out["margin_points"] = round((out["tight"]["median"] - TARGET) * 100, 1)
    out["passed"] = out["tight"]["median"] >= TARGET
    return out


def real_crosscheck(cases: list[dict], baseline: Path = REAL_BASELINE) -> dict | None:
    """So B0 ước lượng với `input_tokens` thật (median 3 lượt) đã có trong baseline lịch sử của noteboard, không gọi API nào."""
    if not baseline.is_file():
        return None
    records = {item["name"]: item for item in json.loads(baseline.read_text(encoding="utf-8")).get("diffs", [])}
    pairs = []
    for case in cases:
        runs = [run["input_tokens"] for run in records.get(case["name"], {}).get("runs", []) if run.get("input_tokens")]
        if eligible(case) and runs:
            pairs.append((case["tokens"]["B0"], statistics.median(runs)))
    if not pairs:
        return None
    ratios = [estimate / real for estimate, real in pairs]
    return {"cases": len(pairs), "median_real_input_tokens": statistics.median(real for _, real in pairs),
            "median_estimate_b0": statistics.median(estimate for estimate, _ in pairs),
            "estimate_over_real": {"median": statistics.median(ratios), "min": min(ratios), "max": max(ratios)}}


def run_ceiling(*, only: tuple[str, ...] | None = None) -> dict:
    """Noteboard, golden set 30 diff sạch (không tính injection: chúng là cặp twin của các diff này)."""
    labels = yaml.safe_load((eval_selector.FIXTURES / "labels.yaml").read_text(encoding="utf-8"))
    cfg = project_lib.load_project("noteboard", ROOT / "configs" / "projects")
    suites = project_lib.load_suites(eval_selector.SUT / cfg["suites_dir"])
    suite_map = project_lib.suites_by_worker(cfg, "pr", suites, registry.load(ROOT / "workers"))
    policy = cfg["modes"]["pr"]
    module_map = yaml.safe_load((eval_selector.SUT / ".qc-agent/ground-truth/module-map.yaml").read_text(encoding="utf-8"))
    request = agent.build_request(suite_map, module_map)
    cap = settings.get().llm_max_input_tokens
    cases = []
    for name in labels:
        if name == "labeled_by" or name.startswith("injection-") or (only is not None and name not in only):
            continue
        with tempfile.TemporaryDirectory(prefix="qc-cost-") as temp:
            checkout = Path(temp) / "sut"
            base, head = eval_selector._repo(eval_selector.FIXTURES / f"{name}.patch", checkout)
            cases.append(measure_case(name, checkout, base, head, request, policy, suite_map, module_map, cap))
    return {"dataset": "noteboard", "token_evidence": EVIDENCE, "mode": "ceiling", "model": settings.get().selector_model, "prompt_version": agent.PROMPT_VERSION,
            "target": TARGET, "prefix_tokens": agent.estimate_request(request, agent.user_message("[]")), "cases_detail": cases, **summarize(cases),
            "real_baseline_crosscheck": real_crosscheck(cases)}


def _pct(value: float) -> str:
    return f"{value * 100:.1f}%"


def render(result: dict) -> str:
    lines = [f"Trần khả thi (ƯỚC LƯỢNG ceil(byte/3), không phải count_tokens) · dataset {result['dataset']} · model {result['model']} · {result['prompt_version']}",
             f"{result['cases']} ca; {result['llm_cases']} đi đường LLM (vào median); loại: {result['excluded']['full_set']} FULL SET, {result['excluded']['floor_only']} chỉ-floor, "
             f"{result['excluded']['token_cap']} token_cap (0 lời gọi LLM, báo riêng)"]
    if not result["llm_cases"]:
        return "\n".join(lines + [result["reason"]])
    med = result["median_tokens"]
    lines += [f"token ở median (ước lượng): A {med['A']:.0f} · P {med['P']:.0f} · B_min {med['B_min']:.0f} · B0 {med['B0']:.0f}",
              f"{'':24}median      P90",
              f"trần lỏng 1-P/A      {_pct(result['loose']['median']):>10}  {_pct(result['loose']['p90']):>8}",
              f"trần chặt 1-B_min/A  {_pct(result['tight']['median']):>10}  {_pct(result['tight']['p90']):>8}   <- quyết định",
              f"đã đạt   1-B0/A      {_pct(result['achieved']['median']):>10}  {_pct(result['achieved']['p90']):>8}",
              f"biên so với {TARGET:.0%}: {result['margin_points']:+.1f} điểm %; ca B_min >= A: {result['b_min_ge_a'] or 'không'}; ca B0 > A: {result['b0_gt_a'] or 'không'}"]
    check = result.get("real_baseline_crosscheck")
    if check:
        ratio = check["estimate_over_real"]
        lines.append(f"đối chiếu số thật lịch sử ({check['cases']} ca, eval/selector-real.json, đo trước S4-03): input_tokens thật median {check['median_real_input_tokens']:.0f} vs "
                     f"B0 ước lượng {check['median_estimate_b0']:.0f}; ước lượng/thật median {ratio['median']:.2f} (min {ratio['min']:.2f}, max {ratio['max']:.2f})")
    lines.append("KẾT LUẬN: " + ("trần chặt >= 40% ở median: điều kiện CẦN, chưa đủ; không tick DoD (chỉ có số ước lượng)."
                                 if result["passed"] else "DỪNG: trần chặt < 40% ở median, không dựng dataset/không tinh chỉnh; chờ chốt mục tiêu (xem prompt S4-04, HỎI TRƯỚC)."))
    return "\n".join(lines)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--ceiling", action="store_true", help="đo trần khả thi 1 - B_min/A trên noteboard (offline, ước lượng)")
    parser.add_argument("--only", help="danh sách ca cách nhau bằng dấu phẩy (kiểm nhanh)")
    parser.add_argument("--out-json", type=Path)
    args = parser.parse_args(argv)
    for stream in (sys.stdout, sys.stderr):
        stream.reconfigure(encoding="utf-8", errors="replace")   # console Windows mặc định cp1252 không in được tiếng Việt
    if not args.ceiling:
        parser.error("hiện chỉ có --ceiling (S4-04 mục 3 mở rộng thêm --dataset/--llm-tokens)")
    try:
        result = run_ceiling(only=tuple(args.only.split(",")) if args.only else None)
    except Exception as error:  # noqa: BLE001 — lỗi dựng repo/đọc fixture là lỗi hệ thống, không phải kết quả đo
        print(f"LỖI: {type(error).__name__}: {error}", file=sys.stderr)
        return 3
    print(render(result))
    if args.out_json:
        args.out_json.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return 0 if result["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
