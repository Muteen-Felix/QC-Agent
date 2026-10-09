"""tools/eval_cost.py (S4-04): công thức, loại ca không gọi LLM khỏi median, báo riêng từng dataset, baseline B0, đường estimate offline và count_tokens qua egress.

Mọi số ở chế độ estimate là ƯỚC LƯỢNG (ceil(byte/3)), không phải count_tokens.
"""
import json
import socket

import httpx
import pytest

from qc_agent.core import egress
from qc_agent.llm.client import LLMError
from tools import eval_cost, selector_datasets
from tools.eval_cost import percentile, reduction, summarize

KEY = "sk-ant-test-cost-eval"


def case(name, a, p, b_min, b0, b1=None, *, route="llm", capped=(), split="tune", cut=(0, 0, 0, 0)):
    return {"name": name, "route": route, "token_cap_at": list(capped), "files": 1, "split": split, "truncated": cut[0], "dropped_hunks": cut[1],
            "baseline_truncated": cut[2], "baseline_dropped_hunks": cut[3], "tokens": {"A": a, "P": p, "B_min": b_min, "B0": b0, "B1": b0 if b1 is None else b1}}


def test_reduction_and_nearest_rank_percentile():
    assert reduction(60, 100) == pytest.approx(.4) and reduction(0, 100) == 1 and reduction(120, 100) == pytest.approx(-.2) and reduction(5, 0) == 0.0
    assert percentile([5, 1, 3, 2, 4], .9) == 5 and percentile([5, 1, 3, 2, 4], .5) == 3 and percentile([7], .9) == 7
    assert percentile(list(range(1, 11)), .9) == 9   # ceil(0.9*10) = 9: giá trị có thật, không nội suy
    with pytest.raises(ValueError):
        percentile([], .5)


def test_median_uses_only_llm_cases_and_excluded_cases_are_listed_separately():
    cases = [case("a", 1000, 900, 940, 1010), case("b", 1000, 900, 960, 1020), case("c", 1000, 900, 920, 1000),
             case("full", 1000, 0, 0, 0, route="full_set"), case("floor", 1000, 0, 0, 0, route="floor_only"),
             case("big", 5000, 900, 960, 4000, capped=["A"])]
    result = summarize(cases)
    assert result["cases"] == 6 and result["llm_cases"] == 3
    assert result["excluded"] == {"full_set": ["full"], "floor_only": ["floor"], "token_cap": [{"name": "big", "at": ["A"]}]}
    assert result["ceiling"]["tight"]["median"] == pytest.approx(.06) and result["ceiling"]["loose"]["median"] == pytest.approx(.10)   # FULL SET (B=0, giảm 100%) không thổi phồng median
    assert result["reduction_b1"]["median"] == pytest.approx(-.01) and result["above_raw"]["b1"] == ["a", "b"]


def test_a_regression_after_tuning_is_reported_by_case_and_in_points():
    cases = [case("a", 1000, 900, 940, 1010, 960), case("b", 1000, 900, 960, 1020, 1030), case("c", 1000, 900, 920, 1000, 950)]
    result = summarize(cases, target=.4)
    assert result["increased_vs_b0"] == ["b"]                                         # B1 > B0 ở ca b dù trung bình có giảm
    assert result["reduction_b0"]["median"] == pytest.approx(-.01) and result["reduction_b1"]["median"] == pytest.approx(.04)
    assert result["delta_points"] == 5.0 and result["target_met"] is False and result["ceiling_ok"] is False


def test_target_none_reports_numbers_but_never_decides_and_exit_code_follows_the_target():
    per = summarize([case("a", 1000, 900, 940, 1010, 960)], target=None)
    assert per["target"] is None and per["target_met"] is None and per["ceiling_ok"] is False
    ok = {**per, "dataset": "x"}
    assert eval_cost.exit_code([ok], ceiling=True) == 0 and eval_cost.exit_code([ok], ceiling=False) == 0     # cost_target null: không quyết exit
    miss = {**summarize([case("a", 1000, 100, 700, 800, 790)], target=.4), "dataset": "y"}
    assert miss["target_met"] is False and eval_cost.exit_code([miss], ceiling=False) == 1
    assert eval_cost.exit_code([{**miss, "ceiling": {"tight": {"median": .3}}}], ceiling=True) == 1            # mục tiêu cao hơn trần chặt: chưa thể đạt theo ước lượng
    hit = {**summarize([case("a", 1000, 100, 500, 800, 550)], target=.4), "dataset": "z"}
    assert hit["target_met"] is True and eval_cost.exit_code([hit], ceiling=False) == 0


def test_the_aggregate_never_hides_a_worse_dataset():
    good = {**summarize([case("a", 1000, 100, 400, 800, 500)], target=.4), "dataset": "good"}
    bad = {**summarize([case("a", 1000, 100, 400, 800, 1100)], target=.4), "dataset": "bad"}
    total = eval_cost.aggregate({"good": good, "bad": bad})
    assert total["note"].startswith("tổng hợp") and bad["target_met"] is False and bad["increased_vs_b0"] == ["a"]
    assert eval_cost.aggregate({"good": good}) is None


def test_nothing_on_the_llm_path_is_not_a_pass():
    result = {**summarize([case("full", 1000, 0, 0, 0, route="full_set")], target=.4), "dataset": "x", "split": "tune", "token_evidence": "estimate", "model": "m",
              "prompt_version": "v", "labels_status": "reviewed", "cost_target": {"median_reduction": .4, "reason": None}}
    assert result["llm_cases"] == 0 and result["target_met"] is None
    assert "không có ca nào" in eval_cost.render(result) and eval_cost.exit_code([result], ceiling=False) == 1


@pytest.fixture
def offline(monkeypatch):
    def refuse(*args, **kwargs):
        raise AssertionError("estimate không được mở kết nối mạng")
    monkeypatch.setattr(socket.socket, "connect", refuse)


def test_estimate_runs_offline_on_real_diffs_and_keeps_the_path_invariant(offline):
    result = eval_cost.run_dataset(selector_datasets.load(), only=("diff-001", "diff-011", "diff-015"))
    assert result["token_evidence"] == "estimate" and result["dataset"] == "noteboard" and result["cost_target"]["median_reduction"] is None
    by_name = {item["name"]: item for item in result["cases_detail"]}
    assert (by_name["diff-001"]["route"], by_name["diff-011"]["route"], by_name["diff-015"]["route"]) == ("llm", "floor_only", "full_set")
    for item in result["cases_detail"]:
        t = item["tokens"]
        assert t["P"] < t["B_min"] <= t["B1"] and t["B0"] == t["B1"]   # chưa có baseline: B0 = B1; danh sách file đầy đủ luôn nặng hơn tiền tố
    assert eval_cost.path_invariant_violations(result) == []
    assert result["llm_cases"] == 1 and result["excluded"]["full_set"] == ["diff-015"] and result["excluded"]["floor_only"] == ["diff-011"]


def test_saved_baseline_is_replayed_as_b0_so_later_runs_do_not_depend_on_the_old_pruner(offline, tmp_path):
    dataset = selector_datasets.load()
    first = eval_cost.run_dataset(dataset, only=("diff-001",))
    document = eval_cost.baseline_document(first)
    assert document["cases"]["diff-001"]["payload_b0"] and document["pruner"]["params"]["per_file_tokens"] == 1500
    document["cases"]["diff-001"]["payload_b0"] += " " * 300                               # giả lập baseline nặng hơn pruner hiện tại
    second = eval_cost.run_dataset(dataset, only=("diff-001",), baseline=document)
    (row,) = second["cases_detail"]
    assert row["tokens"]["B0"] > row["tokens"]["B1"] and second["delta_points"] > 0 and second["increased_vs_b0"] == []


class Deny(egress.EgressPolicy):
    def decide(self, event):
        return egress.Decision("deny", "test")


def _egress_lines(directory):
    return [json.loads(line) for line in (directory / egress.LOG_NAME).read_text(encoding="utf-8").splitlines()]


def _count_transport(seen, total=1700):
    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(json.loads(request.content))
        return httpx.Response(200, json={"input_tokens": total + len(seen)})
    return httpx.MockTransport(handler)


def test_count_mode_goes_through_egress_with_the_request_body_and_counts_each_distinct_payload_once(monkeypatch, tmp_path):
    monkeypatch.setenv("ANTHROPIC_API_KEY", KEY)
    seen = []
    result = eval_cost.run_dataset(selector_datasets.load(), evidence="count", only=("diff-001", "diff-002"), egress_dir=tmp_path / "egress", transport=_count_transport(seen))
    assert result["token_evidence"] == "count_tokens"
    assert all("max_tokens" not in body and body["model"] == result["model"] and body["tools"] for body in seen)
    lines = _egress_lines(tmp_path / "egress")
    assert len(lines) == len(seen) and all(line["categories"] == ["source_code_diff"] for line in lines)
    assert len({body["messages"][0]["content"] if isinstance(body["messages"][0]["content"], str) else json.dumps(body["messages"][0]["content"]) for body in seen}) == len(seen)   # P và payload giống nhau chỉ gửi một lần
    row = result["cases_detail"][0]
    assert row["tokens"]["A"] > 1700 and "count_tokens (số đếm thật" in eval_cost.render(result)


def test_count_mode_with_a_denied_egress_sends_nothing(monkeypatch, tmp_path):
    monkeypatch.setenv("ANTHROPIC_API_KEY", KEY)
    seen = []
    with pytest.raises(LLMError) as caught:
        eval_cost.run_dataset(selector_datasets.load(), evidence="count", only=("diff-001",), egress_dir=tmp_path / "egress", policy=Deny(), transport=_count_transport(seen))
    assert caught.value.kind == "egress_denied" and seen == []


def test_count_mode_refuses_gemini_and_the_cli_will_not_send_without_yes_and_a_key(monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("QC_SELECTOR_MODEL", "gemini-3.6-flash")
    with pytest.raises(ValueError, match="Claude"):
        eval_cost.run_dataset(selector_datasets.load(), evidence="count", only=("diff-001",), egress_dir=tmp_path / "egress", transport=_count_transport([]))
    monkeypatch.delenv("QC_SELECTOR_MODEL")
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    with pytest.raises(SystemExit):
        eval_cost.main(["--llm-tokens", "count", "--only", "diff-001"])                    # thiếu --yes và khoá
    monkeypatch.setenv("ANTHROPIC_API_KEY", KEY)
    with pytest.raises(SystemExit):
        eval_cost.main(["--llm-tokens", "count", "--only", "diff-001"])                    # có khoá nhưng chưa --yes


def test_cli_ceiling_with_a_null_target_exits_zero_and_says_so_and_unknown_dataset_exits_three(tmp_path, capsys):
    out = tmp_path / "ceiling.json"
    code = eval_cost.main(["--ceiling", "--only", "diff-001", "--out-json", str(out)])
    text = capsys.readouterr().out
    data = json.loads(out.read_text(encoding="utf-8"))["datasets"]["noteboard"]
    assert code == 0 and data["token_evidence"] == "estimate" and data["ceiling_ok"] is False and "_payload_b1" not in json.dumps(data)
    assert "ƯỚC LƯỢNG" in text and "chưa có mục tiêu số" in text
    assert eval_cost.main(["--dataset", "không-có-dataset-này"]) == 3


def test_baseline_and_holdout_flags_and_a_foreign_baseline_is_a_system_error(tmp_path, offline):
    first = eval_cost.run_dataset(selector_datasets.load(), only=("diff-001",))
    path = tmp_path / "baseline.json"
    assert eval_cost.main(["--only", "diff-001", "--save-baseline", str(path)]) == 0
    saved = json.loads(path.read_text(encoding="utf-8"))
    assert saved["dataset"] == "noteboard" and saved["cases"]["diff-001"]["sha256"] and first["pruner"]["params"] == saved["pruner"]["params"]
    saved["dataset"] = "other"
    path.write_text(json.dumps(saved), encoding="utf-8")
    assert eval_cost.main(["--only", "diff-001", "--baseline", str(path)]) == 3
    with pytest.raises(SystemExit):
        eval_cost.main(["--dataset", "all", "--baseline", str(path)])                      # baseline chỉ đi với một dataset
