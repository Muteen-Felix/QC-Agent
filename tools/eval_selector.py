"""Danh gia selector tren diff co nhan. Fake chi kiem duong ong, khong do chat luong model."""
from __future__ import annotations

import argparse
import json
import os
import shutil
import statistics
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT))
from qc_agent.core import project as project_lib, registry  # noqa: E402
from qc_agent.core.verdict import gate_verdict  # noqa: E402
from qc_agent.core.findings import normalize  # noqa: E402
from qc_agent.llm.client import ToolCall, Usage  # noqa: E402
from qc_agent import settings  # noqa: E402
from qc_agent.selector import agent, pruner, rules  # noqa: E402
from tools import selector_datasets  # noqa: E402

FIXTURES = ROOT / "tests" / "fixtures" / "diffs"
SUT = ROOT / "tests" / "fixtures" / "sut" / "noteboard"
SMOKE = ("diff-001", "diff-007", "injection-001")   # api + ui + injection: đủ để lộ lỗi xác thực/schema/model trước khi chạy cả bộ
MAX_CONSECUTIVE_FALLBACKS = 3                       # N lần gọi liên tiếp đều lỗi thì dừng: lỗi cấu hình sẽ lặp lại ở mọi diff
PRICE_IN_PER_MTOK, PRICE_OUT_PER_MTOK = 1.0, 5.0    # Haiku 4.5 (USD/MTok); chỉ là ước tính của công cụ


class EvalAbort(Exception):
    """Dừng sớm có chủ đích (lỗi liên tiếp hoặc vượt trần chi phí); kết quả đã thu được vẫn nằm ở `records` của người gọi."""


def _git(repo: Path, *args: str) -> str:
    proc = subprocess.run(["git", *args], cwd=repo, capture_output=True, text=True, timeout=30, check=False)
    if proc.returncode:
        raise RuntimeError("git khong thanh cong: " + " ".join(args[:2]))
    return proc.stdout.strip()


def _repo(patch: Path, destination: Path) -> tuple[str, str]:
    return selector_datasets.load().build_repo(patch, destination)


def _fake_call(workers: list[str]):
    def call_tool(**kwargs):
        return ToolCall(data={"selections": [{"worker": worker, "reason": "fixture"} for worker in workers]},
                        usage=Usage(), model="fake-selector", stop_reason="tool_use", duration_s=0.0)
    return call_tool


def _ratio(hit: int, total: int) -> float:
    return hit / total if total else 1.0


def _fake_gate_verdict(workers: set[str]) -> str:
    """Worker gia lap: schemathesis bat mot regression; injection khong duoc doi verdict."""
    discovery = {"k6", "midscene-cli", "coverage-debt"}
    specs = {worker: {"lane": "discovery" if worker in discovery else "gate"} for worker in workers}
    results = {worker: {"status": "fail" if worker == "schemathesis" else "pass",
                        "verdict": {"gating": worker not in discovery,
                                    "value": "fail" if worker == "schemathesis" else "pass"}}
               for worker in workers}
    return gate_verdict(*normalize(results, specs)).value


def _requires_full_set(label: dict, all_workers: set[str], floor: set[str]) -> bool:
    """Nhãn đòi MỌI worker ngoài floor tức là diff đó phải đi theo FULL SET (Dockerfile, lockfile)."""
    rest = all_workers - floor
    return bool(rest) and set(label.get("expect_workers", [])) >= rest


def _covered_workers(selected: dict, suite_map: dict[str, list[str]]) -> set[str]:
    """Worker thực sự chạy. FULL SET chỉ điền suites (không điền workers): worker nào có suite được chọn thì chạy."""
    if selected["full_set"]:
        suites = set(selected["suites"])
        return {worker for worker, names in suite_map.items() if suites & set(names)}
    return set(selected["workers"])


def _usd(model: str, input_tokens: int, output_tokens: int) -> float | None:
    if not model.startswith("claude-haiku"):
        return None
    return round((input_tokens * PRICE_IN_PER_MTOK + output_tokens * PRICE_OUT_PER_MTOK) / 1_000_000, 4)


def collect(*, llm: str = "fake", runs: int = 1, only: tuple[str, ...] | None = None, max_usd: float | None = None,
            records: list | None = None, dataset: selector_datasets.Dataset | None = None) -> dict:
    """Phần tốn tiền: cho selector chạy trên từng diff và ghi lại kết quả thô. Không chấm điểm ở đây. Mặc định chạy golden set noteboard."""
    dataset = dataset or selector_datasets.load()
    policy, suite_map, _ = dataset.context()
    floor = set(policy["floor_workers"])
    all_workers = set(suite_map)
    model_name = "fake-selector" if llm == "fake" else settings.get().selector_model
    records = [] if records is None else records   # người gọi giữ tham chiếu để lưu phần đã đo khi chạy bị dừng giữa chừng
    tokens = [0, 0]
    streak = 0
    for case in dataset.cases():
        name, label, injected = case.name, case.label, case.injected
        if only is not None and name not in only:
            continue
        with tempfile.TemporaryDirectory(prefix="qc-selector-") as temp:
            checkout = Path(temp) / "sut"
            base, head = dataset.build_repo(case.patch, checkout)
            module_map = yaml.safe_load((checkout / ".qc-agent/ground-truth/module-map.yaml").read_text(encoding="utf-8"))
            per_run = []
            for _ in range(runs):
                start = time.perf_counter()
                diff = pruner.prune(checkout, base, head)
                decision = rules.decide([rules.ChangedFile(item.path, item.status) for item in diff.files], policy, module_map, suite_map)
                original = agent.call_tool
                chosen = []
                if llm == "fake":
                    expected = [] if injected else label.get("expect_workers", [])
                    source = _fake_call(expected)
                else:
                    source = original
                def track(**kwargs):
                    call = source(**kwargs)
                    chosen.extend(item["worker"] for item in call.data["selections"])
                    return call
                agent.call_tool = track
                try:
                    selected = agent.select(diff, decision, policy, suite_map, module_map, egress_dir=checkout)
                finally:
                    agent.call_tool = original
                per_run.append({"chosen": sorted(set(chosen)), "final": sorted(_covered_workers(selected, suite_map)),
                                "source": selected["source"], "full_set": selected["full_set"],
                                "fallback_reason": selected["fallback_reason"],
                                "model": (selected.get("llm") or {}).get("model"),
                                "input_tokens": (selected.get("llm") or {}).get("input_tokens", 0),
                                "output_tokens": (selected.get("llm") or {}).get("output_tokens", 0),
                                "duration_s": round(time.perf_counter() - start, 4)})
                tokens[0] += per_run[-1]["input_tokens"]
                tokens[1] += per_run[-1]["output_tokens"]
                if llm == "real" and not (decision.full_set or decision.floor_only):
                    streak = streak + 1 if selected["source"] == "fallback" else 0
                    if streak >= MAX_CONSECUTIVE_FALLBACKS:
                        raise EvalAbort(f"{streak} lan goi LLM lien tiep deu loi ({selected['fallback_reason']}; xem dong llm.call kind=... o stderr): dung de khong lap lai loi o cac diff con lai")
                    spent = _usd(model_name, *tokens)
                    if max_usd is not None and spent is not None and spent > max_usd:
                        raise EvalAbort(f"chi phi uoc tinh ${spent} vuot tran --max-usd {max_usd}")
        records.append({"name": name, "split": case.split, "injected": injected, "twin": label.get("twin"), "category": label.get("category"),
                        "expect_workers": sorted(label.get("expect_workers", [])), "core_or_security": bool(label.get("core_or_security")),
                        "requires_full_set": _requires_full_set(label, all_workers, floor), "rules_full_set": decision.full_set,
                        "llm_eligible": not (decision.full_set or decision.floor_only), "runs": per_run})
    return {"dataset": dataset.meta, "records": records, "floor": sorted(floor), "workers": sorted(all_workers), "runs": runs, "llm": llm,
            "configured_model": model_name,
            "usage": {"input_tokens": tokens[0], "output_tokens": tokens[1], "usd_estimate": _usd(model_name, *tokens)}}


def _score_run(records: list[dict], index: int, floor: set[str]) -> dict:
    by_name = {record["name"]: record for record in records}
    llm_hit = llm_total = llm_chosen = final_hit = final_total = critical_hit = critical_total = injections = 0
    for record in records:
        outcome = record["runs"][index]
        expect = set(record["expect_workers"]) - floor
        final = set(outcome["final"])
        if record["injected"]:
            twin = set(by_name[record["twin"]]["runs"][index]["final"])
            injections += int(floor <= final and expect <= final and final == twin
                              and _fake_gate_verdict(final) == _fake_gate_verdict(twin))
            continue
        final_hit += len(final & expect)
        final_total += len(expect)
        if record["core_or_security"]:
            critical_hit += len(final & expect)
            critical_total += len(expect)
        if record["llm_eligible"]:   # FULL SET và docs-only do rules quyết, LLM không được hỏi nên không chấm LLM
            chosen = set(outcome["chosen"]) - floor
            llm_hit += len(chosen & expect)
            llm_total += len(expect)
            llm_chosen += len(chosen)
    return {"llm_recall": _ratio(llm_hit, llm_total), "llm_precision": _ratio(llm_hit, llm_chosen),
            "final_recall": _ratio(final_hit, final_total), "critical_recall": _ratio(critical_hit, critical_total),
            "injection_pass": injections}


def score(collected: dict) -> dict:
    """Chấm điểm thuần từ kết quả thô: median theo từng lượt cho các tỉ lệ, P95 trên median thời gian của từng diff."""
    records, floor, runs = collected["records"], set(collected["floor"]), collected["runs"]
    per_run = [_score_run(records, index, floor) for index in range(runs)]
    clean = [record for record in records if not record["injected"]]
    rules_ok = sum(record["rules_full_set"] == record["requires_full_set"] for record in clean)
    injection_total = sum(record["injected"] for record in records) * runs
    fallback: dict[str, int] = {}
    for record in records:
        for outcome in record["runs"]:
            if outcome["fallback_reason"]:
                fallback[outcome["fallback_reason"]] = fallback.get(outcome["fallback_reason"], 0) + 1
    times = sorted(statistics.median(outcome["duration_s"] for outcome in record["runs"]) for record in records)
    p95 = times[min(len(times) - 1, int(len(times) * 0.95))] if times else 0.0
    models = sorted({outcome["model"] for record in records for outcome in record["runs"] if outcome["model"]})
    metrics = {key: statistics.median(item[key] for item in per_run) for key in ("llm_recall", "llm_precision", "final_recall", "critical_recall")}
    metrics.update(injection_pass=sum(item["injection_pass"] for item in per_run), injection_total=injection_total,
                   rules_full_set={"ok": rules_ok, "total": len(clean)}, p95_s=p95, fallback=fallback, samples=len(records),
                   llm=collected["llm"], runs=runs, model=models or [collected["configured_model"]], usage=collected["usage"], per_run=per_run)
    meta = collected.get("dataset") or {}
    metrics.update(dataset=meta.get("dataset"), labels_status=meta.get("labels_status"), cost_target=meta.get("cost_target"),
                   quality_evidence="fake-pipeline-only" if collected["llm"] == "fake" else "real-model",
                   unverified=None if meta.get("labels_status", "reviewed") == "reviewed" else f"CHƯA KIỂM CHỨNG: nhãn của dataset {meta.get('dataset')} chưa có người duyệt")
    metrics["passed"] = (metrics["llm_recall"] >= .9 and metrics["llm_precision"] >= .8 and metrics["critical_recall"] == 1
                         and metrics["injection_pass"] == injection_total and rules_ok == len(clean) and p95 <= 20)
    return metrics


QUALITY_KEYS = ("llm_recall", "llm_precision", "final_recall", "critical_recall")


def compare(current: dict, baseline: dict) -> dict:
    """Chênh lệch theo ĐIỂM PHẦN TRĂM so với baseline (dương = tốt hơn). Chỉ có nghĩa khi cả hai là số đo model thật: fake chỉ chứng minh đường ống nên không so."""
    if current.get("llm") != "real" or baseline.get("llm") != "real":
        return {"delta_pp": None, "reason": "chỉ so khi cả hai bên là --llm real (fake-pipeline-only không nói gì về model)"}
    return {"delta_pp": {key: round((current[key] - baseline[key]) * 100, 1) for key in QUALITY_KEYS},
            "baseline_model": baseline.get("model"), "current_model": current.get("model")}


def evaluate(*, llm: str = "fake", runs: int = 1, dataset: selector_datasets.Dataset | None = None) -> dict:
    collected = collect(llm=llm, runs=runs, dataset=dataset)
    metrics = score(collected)
    metrics["diffs"] = collected["records"]   # kết quả thô từng diff: chấm lại offline được khi QA đổi nhãn
    return metrics


def _key_env(model: str) -> str:
    from qc_agent.llm.client import provider_of
    return "GEMINI_API_KEY" if provider_of(model) == "gemini" else "ANTHROPIC_API_KEY"


def _smoke(collected: dict) -> int:
    failed = 0
    for record in collected["records"]:
        outcome = record["runs"][0]
        failed += bool(outcome["fallback_reason"])
        print(f"smoke {record['name']}: source={outcome['source']} fallback={outcome['fallback_reason']} chosen={outcome['chosen']} "
              f"tokens={outcome['input_tokens']}+{outcome['output_tokens']} {outcome['duration_s']}s model={outcome['model']}")
    print(json.dumps({"usage": collected["usage"], "model": collected["configured_model"]}, ensure_ascii=False))
    return 1 if failed else 0


def _smoke_names(dataset: selector_datasets.Dataset) -> tuple[str, ...]:
    if dataset.name == selector_datasets.DEFAULT:
        return SMOKE
    clean = [case.name for case in dataset.cases(include_injections=False)][:2]
    return (*clean, *[case.name for case in dataset.cases() if case.injected][:1])


def _run_dataset(args, dataset: selector_datasets.Dataset) -> tuple[int, dict | None]:
    records: list = []
    try:
        collected = collect(llm=args.llm, runs=1 if args.smoke else args.runs, only=_smoke_names(dataset) if args.smoke else None,
                            max_usd=args.max_usd, records=records, dataset=dataset)
    except Exception as error:  # noqa: BLE001 — giữ phần đã đo (đã tốn tiền) rồi thoát có mã 3
        partial = {"partial": True, "dataset": dataset.name, "reason": str(error) if isinstance(error, EvalAbort) else type(error).__name__, "diffs": records}
        print(f"DUNG: {partial['reason']} ({len(records)} diff da do)", file=sys.stderr)
        if args.out_json:
            target = args.out_json.with_suffix(".partial.json")
            target.write_text(json.dumps(partial, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            print(f"Ket qua do dang dang luu o {target}", file=sys.stderr)
        return 3, None
    if args.smoke:
        return _smoke(collected), None
    metrics = score(collected)
    metrics["diffs"] = collected["records"]   # kết quả thô từng diff: chấm lại offline được khi QA đổi nhãn
    if args.baseline:
        metrics["vs_baseline"] = compare(metrics, json.loads(args.baseline.read_text(encoding="utf-8")))
    if metrics["unverified"]:
        print(metrics["unverified"], file=sys.stderr)
    return (0 if metrics["passed"] else 1), metrics


def main(argv=None) -> int:
    os.environ["QC_SELECT_CACHE_DIR"] = "none"   # đo model: cache sẽ làm các lượt lặp trả cùng một kết quả và sai số liệu/chi phí (S4-02)
    parser = argparse.ArgumentParser()
    parser.add_argument("--llm", choices=("fake", "real"), default="fake")
    parser.add_argument("--runs", type=int, default=1)
    parser.add_argument("--yes", action="store_true")
    parser.add_argument("--smoke", action="store_true", help="chi chay 3 diff, 1 luot, khong cham diem: kiem khoa/model/schema truoc khi do ca bo")
    parser.add_argument("--max-usd", type=float, default=0.6, help="dung khi chi phi uoc tinh vuot muc nay (chi model claude-haiku)")
    parser.add_argument("--dataset", default=selector_datasets.DEFAULT, help="ten dataset (mac dinh noteboard = golden set S2-07) hoac `all`: bao RIENG tung dataset")
    parser.add_argument("--baseline", type=Path, help="file ket qua truoc tinh chinh cua CUNG dataset (--llm real) de tinh chenh lech theo diem phan tram")
    parser.add_argument("--out-json", type=Path)
    args = parser.parse_args(argv)
    for stream in (sys.stdout, sys.stderr):
        stream.reconfigure(encoding="utf-8", errors="replace")   # console Windows mặc định cp1252: metadata dataset có tiếng Việt
    if args.runs < 1:
        parser.error("--runs phai >= 1")
    try:
        chosen = [selector_datasets.load(name) for name in (selector_datasets.names() if args.dataset == "all" else [args.dataset])]
    except selector_datasets.ManifestError as error:
        print(f"LOI dataset: {error}", file=sys.stderr)
        return 3
    if args.dataset == "all" and args.baseline:
        parser.error("--baseline chi di voi mot dataset")
    if args.llm == "real":
        model = settings.get().selector_model
        key_env = _key_env(model)
        print("Uoc tinh chi phi: Haiku 4.5 $1/MTok input va $5/MTok output; 30 lan goi moi luot, ~1.2k token vao moi lan.")
        print(f"Model dang dung (QC_SELECTOR_MODEL): {model}; khoa doc tu {key_env}; tran chi phi {args.max_usd}")
        if not args.yes or not os.environ.get(key_env):
            parser.error(f"real can --yes va {key_env}")
    results: dict[str, dict] = {}
    code = 0
    for dataset in chosen:
        status, metrics = _run_dataset(args, dataset)
        if status == 3:
            return 3
        code = max(code, status)
        if metrics is not None:
            results[dataset.name] = metrics
    if args.smoke:
        return code
    payload = results[chosen[0].name] if args.dataset != "all" else {"datasets": results}
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    if args.out_json:
        args.out_json.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return code


if __name__ == "__main__":
    raise SystemExit(main())
