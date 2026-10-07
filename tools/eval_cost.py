"""Đo token của TOÀN BỘ request Diff Agent (system + tool + user): diff thô so với diff đã prune, riêng cho từng dataset (S4-04).

Hai loại bằng chứng, luôn ghi trong kết quả (`token_evidence`):
  estimate      offline, `client.estimate_input_tokens` = ceil(byte UTF-8 / 3): ƯỚC LƯỢNG gần đúng, tất định, không gửi gì ra ngoài
  count_tokens  số đếm thật của model Claude nhưng GỬI request ra ngoài (có ghi egress); cần --yes và ANTHROPIC_API_KEY

Payload: A = `git diff` thô trong khung `<untrusted_diff>`; P = tiền tố + danh sách file rỗng; B_min = danh sách file đầy đủ không hunk (cận dưới hợp lệ);
B0 = pruner TRƯỚC tinh chỉnh (đọc từ file baseline do `--save-baseline` lưu, không tính lại bằng mã đã chỉnh); B1 = pruner hiện tại.
Ca FULL SET, chỉ-floor và `token_cap` không gọi LLM nên KHÔNG vào median; mục tiêu giảm (`cost_target`) khai theo từng dataset trong manifest.
Chế độ: `--ceiling` (trần khả thi 1 - B_min/A) hoặc mặc định (mức giảm 1 - B1/A so với B0).
"""
from __future__ import annotations

import argparse
import hashlib
import inspect
import json
import math
import os
import statistics
import subprocess
import sys
import tempfile
from dataclasses import replace
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "src"))
from qc_agent import settings  # noqa: E402
from qc_agent.llm import client, filecache  # noqa: E402
from qc_agent.selector import agent, pruner, rules  # noqa: E402
from tools import selector_datasets  # noqa: E402

CEILING_FLOOR = 0.40   # trần chặt < 40% thì dataset không thể đạt mục tiêu 40% (DoD S4)


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
    run = subprocess.run(["git", "-c", "core.quotepath=off", "diff", "--no-color", "--no-ext-diff", merge_base, head], cwd=checkout, capture_output=True, check=True, timeout=60)
    return run.stdout.decode("utf-8", "replace")


def _minimal_payload(diff) -> str:
    """Danh sách file đầy đủ, không hunk. `truncated`/`dropped_hunks` về giá trị ngắn nhất để là cận dưới thật sự."""
    return agent.payload_json([replace(item, hunks=None, truncated=False, dropped_hunks=0) for item in diff.files])


def pruner_info() -> dict:
    """Pruner đang đo: commit cuối chạm `pruner.py` và tham số mặc định (không phải số token thật của mã)."""
    sha = subprocess.run(["git", "log", "-1", "--format=%H", "--", "src/qc_agent/selector/pruner.py"], cwd=ROOT, capture_output=True, text=True, check=False).stdout.strip()
    dirty = bool(subprocess.run(["git", "status", "--porcelain", "--", "src/qc_agent/selector/pruner.py"], cwd=ROOT, capture_output=True, text=True, check=False).stdout.strip())
    defaults = {name: p.default for name, p in inspect.signature(pruner.prune).parameters.items() if p.default is not inspect.Parameter.empty}
    return {"sha": sha or None, "uncommitted_changes": dirty, "params": defaults}


def make_counter(evidence: str, request: agent.DiffRequest, model: str, egress_dir: Path, *, policy=None, transport=None):
    """`estimate`: offline. `count`: `count_tokens` (GỬI ra ngoài; chỉ model Claude; ghi egress trước khi gửi, `policy` deny thì không có HTTP)."""
    if evidence == "estimate":
        return lambda user: agent.estimate_request(request, user)
    if client.provider_of(model) == "gemini":
        raise ValueError("count_tokens chỉ có cho model Claude (QC_SELECTOR_MODEL là gemini-*)")

    def count(user: str) -> int:
        return client.count_tokens(purpose="cost-eval", model=model, system=request.system, user=user, tool_name=agent.TOOL_NAME,
                                   tool_description=agent.TOOL_DESCRIPTION, input_schema=request.schema, egress_dir=egress_dir,
                                   data_categories=["source_code_diff"], policy=policy, transport=transport)
    return count


def measure_case(case, dataset, checkout: Path, base: str, head: str, request: agent.DiffRequest, counter, cap: int,
                 baseline_case: dict | None = None) -> dict:
    policy, suite_map, module_map = dataset.context()
    diff = pruner.prune(checkout, base, head)
    decision = rules.decide([rules.ChangedFile(item.path, item.status) for item in diff.files], policy, module_map, suite_map)
    payload_b1 = agent.pruned_payload(diff)
    users = {"A": agent.user_message(_raw_diff(checkout, diff.merge_base, head)), "P": agent.user_message("[]"),
             "B_min": agent.user_message(_minimal_payload(diff)), "B1": agent.user_message(payload_b1)}
    users["B0"] = agent.user_message(baseline_case["payload_b0"]) if baseline_case else users["B1"]   # chưa có baseline: B0 = B1 (chưa chỉnh)
    tokens = {}
    for key, user in users.items():
        tokens[key] = tokens["B1"] if key == "B0" and not baseline_case else counter(user)
    route = "full_set" if decision.full_set else "floor_only" if decision.floor_only else "llm"
    return {"name": case.name, "split": case.split, "route": route, "files": len(diff.files), "tokens": tokens,
            "token_cap_at": [key for key in ("A", "B0", "B1") if tokens[key] > cap],
            "truncated": sum(item.truncated for item in diff.files), "dropped_hunks": sum(item.dropped_hunks for item in diff.files),
            "baseline_truncated": baseline_case["truncated"] if baseline_case else None,
            "baseline_dropped_hunks": baseline_case["dropped_hunks"] if baseline_case else None,
            "_payload_b1": payload_b1, "_paths_pruned": sorted(item.path for item in diff.files), "_paths_raw": None}


def eligible(case: dict) -> bool:
    return case["route"] == "llm" and not case["token_cap_at"]


def _spread(values: list[float]) -> dict:
    return {"median": statistics.median(values), "p90": percentile(values, .9)}


def _excluded(cases: list[dict]) -> dict:
    return {"full_set": [c["name"] for c in cases if c["route"] == "full_set"], "floor_only": [c["name"] for c in cases if c["route"] == "floor_only"],
            "token_cap": [{"name": c["name"], "at": c["token_cap_at"]} for c in cases if c["route"] == "llm" and c["token_cap_at"]]}


def summarize(cases: list[dict], target: float | None = None) -> dict:
    """Số liệu của MỘT dataset (một split). Chỉ ca đi đường LLM và không dính `token_cap` vào median/P90; ca còn lại có danh sách riêng."""
    used = [case for case in cases if eligible(case)]
    out = {"cases": len(cases), "llm_cases": len(used), "excluded": _excluded(cases), "target": target}
    if not used:
        return {**out, "reason": "không có ca nào đi đường LLM", "target_met": None, "ceiling_ok": None}
    t = lambda c, key: c["tokens"][key]   # noqa: E731
    out["tokens"] = {key: _spread([t(c, key) for c in used]) for key in ("A", "P", "B_min", "B0", "B1")}
    out["ceiling"] = {"loose": _spread([reduction(t(c, "P"), t(c, "A")) for c in used]), "tight": _spread([reduction(t(c, "B_min"), t(c, "A")) for c in used])}
    out["reduction_b0"] = _spread([reduction(t(c, "B0"), t(c, "A")) for c in used])
    out["reduction_b1"] = _spread([reduction(t(c, "B1"), t(c, "A")) for c in used])
    out["delta_points"] = round((out["reduction_b1"]["median"] - out["reduction_b0"]["median"]) * 100, 1)
    out["increased_vs_b0"] = [c["name"] for c in used if t(c, "B1") > t(c, "B0")]
    out["above_raw"] = {"b0": [c["name"] for c in used if t(c, "B0") > t(c, "A")], "b1": [c["name"] for c in used if t(c, "B1") > t(c, "A")]}
    out["b_min_ge_a"] = [c["name"] for c in used if t(c, "B_min") >= t(c, "A")]
    out["cut"] = {"b0": sum(1 for c in cases if (c["baseline_truncated"] or 0) or (c["baseline_dropped_hunks"] or 0)),
                  "b1": sum(1 for c in cases if c["truncated"] or c["dropped_hunks"])}
    out["margin_points"] = round((out["ceiling"]["tight"]["median"] - CEILING_FLOOR) * 100, 1)
    out["ceiling_ok"] = out["ceiling"]["tight"]["median"] >= CEILING_FLOOR
    out["target_met"] = None if target is None else out["reduction_b1"]["median"] >= target
    return out


def aggregate(per_dataset: dict[str, dict]) -> dict | None:
    """Số tổng hợp chỉ để tham khảo: không bao giờ dùng để kết luận một dataset (một dataset xấu đi vẫn lộ ở dòng riêng của nó)."""
    rows = [(name, s) for name, s in per_dataset.items() if s.get("llm_cases")]
    if len(rows) < 2:
        return None
    weights = [(s["llm_cases"], s["reduction_b1"]["median"]) for _, s in rows]
    return {"datasets": [name for name, _ in rows], "llm_cases": sum(w for w, _ in weights),
            "median_of_dataset_medians_b1": statistics.median(m for _, m in weights), "note": "tổng hợp, không thay kết luận từng dataset"}


def real_crosscheck(cases: list[dict], baseline: Path) -> dict | None:
    """So B1 ước lượng với `input_tokens` thật (median 3 lượt) trong baseline lịch sử của noteboard, không gọi API nào. Chỉ dùng được khi B1 chưa chỉnh (== B0)."""
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
    return {"cases": len(pairs), "median_real_input_tokens": statistics.median(real for _, real in pairs), "median_estimate_b0": statistics.median(e for e, _ in pairs),
            "estimate_over_real": {"median": statistics.median(ratios), "min": min(ratios), "max": max(ratios)}}


REAL_BASELINE = ROOT / "eval" / "selector-real.json"


def run_dataset(dataset, *, evidence: str = "estimate", split: str = "tune", only: tuple[str, ...] | None = None, baseline: dict | None = None,
                counter=None, egress_dir: Path | None = None, policy=None, transport=None) -> dict:
    """Đo một dataset: dựng repo tạm cho từng ca, đo A/P/B_min/B0/B1, kiểm bất biến danh sách file. `split`: tune | holdout | all (holdout chỉ nên chạy MỘT lần sau khi đóng băng núm)."""
    _, suite_map, module_map = dataset.context()
    request = agent.build_request(suite_map, module_map)
    cap = settings.get().llm_max_input_tokens
    model = settings.get().selector_model
    raw_counter = counter or make_counter(evidence, request, model, egress_dir or Path(tempfile.mkdtemp(prefix="qc-cost-egress-")), policy=policy, transport=transport)
    memo: dict[str, int] = {}

    def counter(user: str) -> int:   # cùng một chuỗi chỉ đếm một lần (tiền tố P giống nhau ở mọi ca; count_tokens thì khỏi gửi lặp)
        if user not in memo:
            memo[user] = raw_counter(user)
        return memo[user]
    cases = []
    for case in dataset.cases(split=None if split == "all" else split, include_injections=False):
        if only is not None and case.name not in only:
            continue
        with tempfile.TemporaryDirectory(prefix="qc-cost-") as temp:
            checkout = Path(temp) / "sut"
            base, head = dataset.build_repo(case.patch, checkout)
            row = measure_case(case, dataset, checkout, base, head, request, counter, cap, (baseline or {}).get("cases", {}).get(case.name))
            raw_paths = subprocess.run(["git", "-c", "core.quotepath=off", "diff", "--name-only", "-z", "-M", row_merge_base(checkout, base, head), head], cwd=checkout,
                                       capture_output=True, check=True).stdout.decode("utf-8", "replace").split("\0")
            row["_paths_raw"] = sorted(path for path in raw_paths if path)
            cases.append(row)
    summary = summarize(cases, dataset.cost_target["median_reduction"])
    return {"dataset": dataset.name, "split": split, "token_evidence": "count_tokens" if evidence == "count" else "estimate", "model": model, "prompt_version": agent.PROMPT_VERSION,
            "labels_status": dataset.labels_status, "cost_target": dataset.cost_target, "prefix_tokens": counter(agent.user_message("[]")),
            "pruner": pruner_info(), "baseline_pruner": (baseline or {}).get("pruner"), "policy_sha": filecache.digest(dataset.context()[0]),
            "module_map_sha": filecache.digest(dataset.context()[2]), "cases_detail": cases, **summary}


def row_merge_base(checkout: Path, base: str, head: str) -> str:
    return subprocess.run(["git", "merge-base", base, head], cwd=checkout, capture_output=True, text=True, check=True).stdout.strip()


def path_invariant_violations(result: dict) -> list[str]:
    """Danh sách file của payload đã prune phải bằng danh sách file của diff thô (đổi tên: tính theo đường dẫn MỚI). Trả về tên ca vi phạm."""
    return [c["name"] for c in result["cases_detail"] if c["_paths_raw"] is not None and c["_paths_pruned"] != c["_paths_raw"]]


def baseline_document(result: dict) -> dict:
    """File baseline: payload B0 của từng ca (để đo lại bằng estimate HOẶC count_tokens mà không cần mã pruner cũ) + SHA/tham số của pruner lúc đo."""
    return {"dataset": result["dataset"], "split": result["split"], "token_evidence_when_saved": result["token_evidence"], "pruner": result["pruner"],
            "cases": {c["name"]: {"payload_b0": c["_payload_b1"], "truncated": c["truncated"], "dropped_hunks": c["dropped_hunks"],
                                  "sha256": hashlib.sha256(c["_payload_b1"].encode("utf-8")).hexdigest()} for c in result["cases_detail"]}}


def public(result: dict) -> dict:
    """Bản ghi ra --out-json: bỏ payload và danh sách đường dẫn nội bộ."""
    out = dict(result)
    out["cases_detail"] = [{k: v for k, v in c.items() if not k.startswith("_")} for c in result["cases_detail"]]
    return out


def _pct(value: float) -> str:
    return f"{value * 100:.1f}%"


def render(result: dict, *, ceiling: bool = False) -> str:
    ev = "ƯỚC LƯỢNG ceil(byte/3), không phải count_tokens" if result["token_evidence"] == "estimate" else "count_tokens (số đếm thật, đã gửi dữ liệu ra ngoài)"
    head = f"[{result['dataset']}] split {result['split']} · {ev} · model {result['model']} · {result['prompt_version']} · nhãn {result['labels_status']}"
    lines = [head]
    if result["labels_status"] != "reviewed":
        lines.append(f"CHƯA KIỂM CHỨNG: nhãn của {result['dataset']} chưa có người duyệt (số recall/rules_full_set của dataset này không được coi là đạt)")
    ex = result["excluded"]
    lines.append(f"{result['cases']} ca; {result['llm_cases']} đi đường LLM (vào median). Không vào median (0 lời gọi LLM): {len(ex['full_set'])} FULL SET, "
                 f"{len(ex['floor_only'])} chỉ-floor, {len(ex['token_cap'])} token_cap {[(e['name'], e['at']) for e in ex['token_cap']] or ''}".rstrip())
    if not result["llm_cases"]:
        return "\n".join(lines + [result["reason"]])
    tok = result["tokens"]
    lines.append("token ở median (P90): " + " · ".join(f"{k} {tok[k]['median']:.0f} ({tok[k]['p90']:.0f})" for k in ("A", "P", "B_min", "B0", "B1")))
    lines.append(f"{'':26}median      P90")
    lines.append(f"trần lỏng  1-P/A       {_pct(result['ceiling']['loose']['median']):>10}  {_pct(result['ceiling']['loose']['p90']):>8}")
    lines.append(f"trần chặt  1-B_min/A   {_pct(result['ceiling']['tight']['median']):>10}  {_pct(result['ceiling']['tight']['p90']):>8}   (biên so với 40%: {result['margin_points']:+.1f} điểm %)")
    lines.append(f"trước chỉnh 1-B0/A     {_pct(result['reduction_b0']['median']):>10}  {_pct(result['reduction_b0']['p90']):>8}")
    if not ceiling:
        lines.append(f"hiện tại    1-B1/A     {_pct(result['reduction_b1']['median']):>10}  {_pct(result['reduction_b1']['p90']):>8}   (so với B0: {result['delta_points']:+.1f} điểm %)")
        lines.append(f"ca B1 > B0 (tăng token): {result['increased_vs_b0'] or 'không'}; ca B > A (to hơn diff thô): B0 {result['above_raw']['b0'] or 'không'}, B1 {result['above_raw']['b1'] or 'không'}")
        lines.append(f"ca bị cắt (truncated/dropped_hunks > 0): B0 {result['cut']['b0']}, B1 {result['cut']['b1']}")
    target = result["target"]
    if target is None:
        lines.append(f"mục tiêu: N/A đã chốt ({result['cost_target']['reason']})")
    else:
        lines.append(f"mục tiêu {target:.0%}: " + ("ĐẠT (theo số ước lượng, chưa đủ để tick DoD)" if result["target_met"] else "CHƯA ĐẠT")
                     + ("" if result["ceiling_ok"] else f"; trần chặt {_pct(result['ceiling']['tight']['median'])} < mục tiêu: không khả thi"))
    return "\n".join(lines)


def exit_code(results: list[dict], *, ceiling: bool) -> int:
    """Dataset có `cost_target` mà không đạt (hoặc trần chặt thấp hơn mục tiêu) thì 1; dataset `cost_target: null` chỉ báo số."""
    bad = []
    for result in results:
        if result["target"] is None:
            continue
        if not result["llm_cases"] or (result["ceiling"]["tight"]["median"] < result["target"] if ceiling else not result["target_met"]):
            bad.append(result["dataset"])
    if bad:
        print("KHÔNG ĐẠT mục tiêu: " + ", ".join(bad), file=sys.stderr)
    return 1 if bad else 0


def main(argv=None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dataset", default=selector_datasets.DEFAULT, help="tên dataset hoặc `all` (báo RIÊNG từng dataset)")
    parser.add_argument("--ceiling", action="store_true", help="đo trần khả thi 1 - B_min/A thay vì mức giảm của pruner")
    parser.add_argument("--llm-tokens", choices=("estimate", "count"), default="estimate", help="estimate: offline; count: count_tokens (gửi dữ liệu ra ngoài, cần --yes)")
    parser.add_argument("--split", choices=("tune", "holdout", "all"), default="tune", help="holdout chỉ chạy MỘT lần sau khi đóng băng núm")
    parser.add_argument("--baseline", type=Path, help="file do --save-baseline lưu TRƯỚC tinh chỉnh (một dataset)")
    parser.add_argument("--save-baseline", type=Path, help="lưu payload B0 của pruner hiện tại (một dataset) để các lần đo sau so sánh")
    parser.add_argument("--only", help="danh sách ca cách nhau bằng dấu phẩy (kiểm nhanh)")
    parser.add_argument("--yes", action="store_true")
    parser.add_argument("--out-json", type=Path)
    args = parser.parse_args(argv)
    for stream in (sys.stdout, sys.stderr):
        stream.reconfigure(encoding="utf-8", errors="replace")   # console Windows mặc định cp1252 không in được tiếng Việt
    if args.dataset == "all" and (args.baseline or args.save_baseline):
        parser.error("--baseline/--save-baseline chỉ đi với một dataset")
    if args.llm_tokens == "count":
        key_env = "ANTHROPIC_API_KEY"
        print(f"count_tokens GỬI toàn bộ request (kèm nội dung diff) tới API: model {settings.get().selector_model}, mỗi ca ~5 lời gọi, có ghi egress.")
        if not args.yes or not os.environ.get(key_env):
            parser.error(f"count cần --yes và {key_env}")
    try:
        datasets = [selector_datasets.load(name) for name in (selector_datasets.names() if args.dataset == "all" else [args.dataset])]
        baseline = json.loads(args.baseline.read_text(encoding="utf-8")) if args.baseline else None
        if baseline and baseline.get("dataset") != datasets[0].name:
            raise selector_datasets.ManifestError("file baseline thuộc dataset khác")
        results = [run_dataset(d, evidence=args.llm_tokens, split=args.split, only=tuple(args.only.split(",")) if args.only else None, baseline=baseline) for d in datasets]
    except Exception as error:  # noqa: BLE001 — lỗi dựng repo/đọc fixture/manifest là lỗi hệ thống, không phải kết quả đo
        print(f"LỖI: {type(error).__name__}: {error}", file=sys.stderr)
        return 3
    for result in results:
        print(render(result, ceiling=args.ceiling) + "\n")
        for name in path_invariant_violations(result):
            print(f"VI PHẠM: danh sách file của payload đã prune khác diff thô ở ca {name}", file=sys.stderr)
    total = aggregate({r["dataset"]: r for r in results})
    if total:
        print(f"Tổng hợp (chỉ tham khảo, không thay kết luận từng dataset): {total}")
    if args.save_baseline:
        args.save_baseline.write_text(json.dumps(baseline_document(results[0]), ensure_ascii=False) + "\n", encoding="utf-8")
    if args.out_json:
        args.out_json.write_text(json.dumps({"datasets": {r["dataset"]: public(r) for r in results}, "aggregate": total}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    violated = any(path_invariant_violations(r) for r in results)
    return 1 if violated else exit_code(results, ceiling=args.ceiling)


if __name__ == "__main__":
    raise SystemExit(main())
