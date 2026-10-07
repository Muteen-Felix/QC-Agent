"""tools/eval_cost.py --ceiling (S4-04 mục 0): công thức, loại ca không gọi LLM khỏi median, chạy offline. Mọi số ở đây là ƯỚC LƯỢNG (estimate), không phải count_tokens."""
import json
import socket

import pytest

from tools import eval_cost
from tools.eval_cost import percentile, reduction, summarize


def case(name, a, p, b_min, b0, *, route="llm", capped=()):
    return {"name": name, "route": route, "token_cap_at": list(capped), "files": 1, "truncated": 0, "dropped_hunks": 0,
            "tokens": {"A": a, "P": p, "B_min": b_min, "B0": b0}}


def test_reduction_and_nearest_rank_percentile():
    assert reduction(60, 100) == pytest.approx(.4) and reduction(0, 100) == 1 and reduction(120, 100) == pytest.approx(-.2) and reduction(5, 0) == 0.0
    assert percentile([5, 1, 3, 2, 4], .9) == 5 and percentile([5, 1, 3, 2, 4], .5) == 3 and percentile([7], .9) == 7
    assert percentile(list(range(1, 11)), .9) == 9   # ceil(0.9*10) = 9: giá trị có thật, không nội suy
    with pytest.raises(ValueError):
        percentile([], .5)


def test_median_uses_only_llm_cases_and_excluded_cases_are_counted_separately():
    cases = [case("a", 1000, 900, 940, 1010), case("b", 1000, 900, 960, 1020), case("c", 1000, 900, 920, 1000),
             case("full", 1000, 0, 0, 0, route="full_set"), case("floor", 1000, 0, 0, 0, route="floor_only"),
             case("big", 5000, 900, 960, 4000, capped=["A"])]
    result = summarize(cases)
    assert result["cases"] == 6 and result["llm_cases"] == 3 and result["excluded"] == {"full_set": 1, "floor_only": 1, "token_cap": 1}
    assert result["tight"]["median"] == pytest.approx(.06) and result["loose"]["median"] == pytest.approx(.10)   # FULL SET (B=0, giảm 100%) không thổi phồng median
    assert result["achieved"]["median"] == pytest.approx(-.01) and result["b0_gt_a"] == ["a", "b"]


def test_gate_is_decided_by_the_tight_ceiling_not_the_loose_one():
    below = summarize([case("a", 1000, 100, 700, 800)])                 # trần lỏng 90% nhưng danh sách file buộc phải có: trần chặt 30%
    assert below["loose"]["median"] == pytest.approx(.9) and below["tight"]["median"] == pytest.approx(.3) and below["passed"] is False
    assert below["margin_points"] == pytest.approx(-10.0)
    assert summarize([case("a", 1000, 100, 500, 600)])["passed"] is True


def test_nothing_on_the_llm_path_is_not_a_pass():
    result = summarize([case("full", 1000, 0, 0, 0, route="full_set")])
    assert result["passed"] is False and result["llm_cases"] == 0 and "reason" in result
    assert "không có ca nào" in eval_cost.render({**result, "dataset": "x", "model": "m", "prompt_version": "v"})


def test_ceiling_runs_offline_on_real_diffs_and_keeps_the_ordering_of_payloads(monkeypatch):
    def refuse(*args, **kwargs):
        raise AssertionError("estimate không được mở kết nối mạng")
    monkeypatch.setattr(socket.socket, "connect", refuse)
    result = eval_cost.run_ceiling(only=("diff-001", "diff-011", "diff-015"))
    assert result["token_evidence"] == "estimate" and result["dataset"] == "noteboard"
    by_name = {item["name"]: item for item in result["cases_detail"]}
    assert (by_name["diff-001"]["route"], by_name["diff-011"]["route"], by_name["diff-015"]["route"]) == ("llm", "floor_only", "full_set")
    for item in result["cases_detail"]:
        t = item["tokens"]
        assert t["P"] < t["B_min"] <= t["B0"]   # danh sách file đầy đủ luôn nặng hơn tiền tố; pruner không thể nhỏ hơn cận dưới
    assert result["llm_cases"] == 1 and result["excluded"] == {"full_set": 1, "floor_only": 1, "token_cap": 0}


def test_cli_exit_codes_and_json_output(tmp_path, capsys):
    out = tmp_path / "ceiling.json"
    code = eval_cost.main(["--ceiling", "--only", "diff-001", "--out-json", str(out)])
    text = capsys.readouterr().out
    data = json.loads(out.read_text(encoding="utf-8"))
    assert code == 1 and data["passed"] is False and data["token_evidence"] == "estimate"   # noteboard hiện dưới 40%: cổng báo DỪNG bằng exit 1
    assert "ƯỚC LƯỢNG" in text and "DỪNG" in text and "count_tokens" in text
    assert eval_cost.main(["--ceiling", "--only", "không-có-ca-này"]) == 1                    # không ca nào chạy: không được coi là đạt
    with pytest.raises(SystemExit):
        eval_cost.main([])                                                                   # chưa có chế độ nào khác ở bước 0
