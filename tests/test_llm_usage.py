"""S4-03: bảng giá duy nhất, `est_usd`, `llm_usage.json` của GT (từng lời gọi, kể cả lần bị từ chối và lần chưa rõ chi phí), trần token, dòng chi phí.

LLM là `FakeAnthropic` hoặc `call_tool` giả; không có mạng thật.
"""
import copy
import importlib.util
import json
import sys
from pathlib import Path

import pytest

from qc_agent import costing
from qc_agent.groundtruth import generate as gt_generate
from qc_agent.llm import agent_loop as al
from qc_agent.llm import client, prices
from qc_agent.llm.client import LLMError, Usage
from tests.fakes import FakeAnthropic
from tests.test_gt_agent import full_script, emitted, prd, spec  # noqa: F401  (fixtures + dựng script của agent)
from tests.test_gt_cli import GT, KEY, OPENAPI, PRD_FILE, RESPONSE, env, fake, generate_args, generated, gt, make_sut  # noqa: F401
from tests.test_gt_cli_agent import served  # noqa: F401

ROOT = Path(__file__).resolve().parent.parent
MARKER = "PRIVATE_PRD_MARKER_88a"


def egress_file(tmp_path, name, base="egress"):
    return tmp_path / f"{base}-{name}" / "llm_usage.json"


def rows_of(path):
    return json.loads(path.read_text(encoding="utf-8"))


# ---------------- bảng giá ----------------

def test_est_usd_uses_all_four_usage_fields():
    one_million = Usage(1_000_000, 100_000, 200_000, 3_000_000)
    # claude-sonnet-5: vào 2, ra 10, đọc cache 0,2; ghi cache = 1,25 × vào
    assert prices.estimate_cost("claude-sonnet-5", one_million) == pytest.approx(1_000_000 * 2 / 1e6 + 100_000 * 10 / 1e6 + 200_000 * 2 * 1.25 / 1e6 + 3_000_000 * 0.2 / 1e6)
    assert prices.estimate_cost("claude-haiku-4-5-20251001", Usage(1_000_000, 1_000_000, 1_000_000, 1_000_000)) == pytest.approx(1 + 5 + 1.25 + 0.1)
    assert prices.estimate_cost("claude-sonnet-5", Usage()) == 0.0


@pytest.mark.parametrize("model", ["some-future-model", "gemini-3.6-flash", "Gemini-3.5-flash-lite", "", "claude"])
def test_unknown_and_gemini_models_have_no_price_and_are_never_guessed(model):
    assert prices.estimate_cost(model, Usage(1000, 1000, 1000, 1000)) is None


def test_the_longest_prefix_wins_and_the_table_is_ordered_longest_first():
    assert prices.estimate_cost("claude-sonnet-5-5-20260101", Usage(1_000_000)) == pytest.approx(2.0)
    assert prices.PRICES_DATE == "2026-09-25" and prices._PRICE_KEYS == sorted(prices.PRICES, key=len, reverse=True)


def test_agent_loop_and_the_eval_tool_use_the_single_table_from_prices():
    assert al.PRICES is prices.PRICES and al.estimate_cost is prices.estimate_cost
    spec_ = importlib.util.spec_from_file_location("eval_groundtruth_s43", ROOT / "tools" / "eval_groundtruth.py")
    ev = importlib.util.module_from_spec(spec_)
    sys.modules["eval_groundtruth_s43"] = ev
    spec_.loader.exec_module(ev)
    assert ev.PRICES_PER_MTOK == {model: (price[0], price[1]) for model, price in prices.PRICES.items()}   # view suy ra, không phải bảng thứ hai
    assert ev.PRICES_PER_MTOK["claude-sonnet-5"] == (2.0, 10.0)


def test_there_is_exactly_one_price_table_in_the_source_tree():
    hits = [path.relative_to(ROOT).as_posix() for path in (ROOT / "src").rglob("*.py") if '"claude-haiku-4-5": (1.0' in path.read_text(encoding="utf-8")]
    assert hits == ["src/qc_agent/llm/prices.py"]


# ---------------- costing: dòng và tổng ----------------

def test_row_shape_and_the_meaning_of_null():
    full = costing.row(purpose="gt-generate", model="m", prompt_version="v/1", usage={"input_tokens": 1, "output_tokens": 2, "cache_creation_input_tokens": 3, "cache_read_input_tokens": 4},
                       est_usd=0.5, duration_s=1.25)
    assert full == {"purpose": "gt-generate", "model": "m", "prompt_version": "v/1", "input_tokens": 1, "output_tokens": 2, "cache_creation_input_tokens": 3,
                    "cache_read_input_tokens": 4, "est_usd": 0.5, "cache_hit": False, "duration_s": 1.25}
    unknown = costing.row(purpose="gt-generate", model="m", prompt_version="v/1", usage=None, est_usd=0.5, status="timeout", unknown_calls=1, duration_s=300.0)
    assert unknown["usage_known"] is False and unknown["unknown_calls"] == 1 and unknown["status"] == "timeout" and unknown["duration_s"] == 300.0
    assert all(unknown[k] is None for k in ("input_tokens", "output_tokens", "cache_creation_input_tokens", "cache_read_input_tokens", "est_usd"))   # null, không phải 0
    agent = costing.row(purpose="gt-agent", model="m", prompt_version="v", usage={"input_tokens": 1}, est_usd=None, turns=7)
    assert agent["turns"] == 7 and agent["est_usd"] is None and agent["output_tokens"] == 0


def test_summarize_skips_cache_hits_and_counts_unknown_calls_and_unpriced_models():
    usage = {"input_tokens": 100, "output_tokens": 10, "cache_creation_input_tokens": 50, "cache_read_input_tokens": 400}
    rows = [costing.row(purpose="a", model="claude-sonnet-5", prompt_version="v", usage=usage, est_usd=0.25),
            costing.row(purpose="a", model="claude-sonnet-5", prompt_version="v", usage=usage, est_usd=0.25, cache_hit=True),
            costing.row(purpose="a", model="gemini-3.6-flash", prompt_version="v", usage=usage, est_usd=None),
            costing.row(purpose="a", model="claude-sonnet-5", prompt_version="v", usage=None, est_usd=None, status="timeout", unknown_calls=1)]
    total = costing.summarize(rows)
    assert total == {"calls": 4, "cache_hits": 1, "prompt_tokens": 1100, "cache_read_tokens": 800, "output_tokens": 20, "est_usd": 0.25, "priced_calls": 1,
                     "unknown_calls": 1, "unpriced_models": ["gemini-3.6-flash"]}


def test_cost_line_formats(monkeypatch):
    base = dict(elapsed="1m02s", summary=costing.summarize([]))
    assert costing.cost_line(**base) == "wallclock 1m02s · LLM: 0 in (0 từ cache) / 0 out · ~$0.00"
    tiny = costing.summarize([costing.row(purpose="a", model="m", prompt_version="v", usage={"input_tokens": 1200}, est_usd=0.0012)])
    assert costing.cost_line(elapsed="3s", summary=tiny) == "wallclock 3s · LLM: 1 200 in (0 từ cache) / 0 out · ~$0.0012"   # nhỏ hơn 1 cent vẫn không làm tròn về $0.00
    unknown = costing.summarize([costing.row(purpose="a", model="m", prompt_version="v", usage=None, est_usd=None, status="timeout", unknown_calls=2)])
    assert costing.cost_line(elapsed="3s", summary=unknown) == "wallclock 3s · LLM: 0 in (0 từ cache) / 0 out · ~$? (chưa rõ)"
    assert "$0.00" not in costing.cost_line(elapsed="3s", summary=unknown, worker_usd=0.0)
    assert costing.cost_line(elapsed="3s", summary=unknown, worker_usd=0.5).endswith("~$0.50 (+2 lời gọi timeout/lỗi mạng chưa rõ chi phí)")   # phần đã biết là cận dưới


# ---------------- GT một lời gọi: llm_usage.json ----------------

def test_gt_generate_writes_one_row_per_call_without_any_content(tmp_path, capsys, fake):
    prd_text = PRD_FILE.read_text(encoding="utf-8") + f"\n{MARKER}\n"
    sut = make_sut(tmp_path, "one", prd_text=prd_text)
    summary = tmp_path / "summary.json"
    code, out, err = gt(capsys, *generate_args(sut, "--summary-json", summary, egress=tmp_path / "egress-one"))
    assert code == 0, out + err
    path = egress_file(tmp_path, "one")
    (row,) = rows_of(path)
    assert row["purpose"] == "gt-generate" and row["model"] == "claude-sonnet-5" and row["prompt_version"] == "gt-generate/1" and row["cache_hit"] is False
    assert (row["input_tokens"], row["output_tokens"]) == (4210, 9350) and isinstance(row["duration_s"], float) and isinstance(row["est_usd"], float) and "status" not in row
    assert row["est_usd"] == pytest.approx(prices.estimate_cost("claude-sonnet-5", Usage(4210, 9350)))
    assert MARKER not in path.read_text(encoding="utf-8")
    data = json.loads(summary.read_text(encoding="utf-8"))
    assert data["est_usd"] == pytest.approx(row["est_usd"]) and data["unknown_calls"] == 0


def bad_answer(output_tokens=77):
    response = copy.deepcopy(json.loads(RESPONSE.read_text(encoding="utf-8")))
    response["content"][0]["input"] = {"summary": "không đúng schema"}
    response["usage"] = {"input_tokens": 1000, "output_tokens": output_tokens, "cache_creation_input_tokens": 0, "cache_read_input_tokens": 0}
    return response


def test_a_rejected_first_answer_is_still_counted_in_the_usage_file_and_the_summary(tmp_path, capsys, monkeypatch):
    with FakeAnthropic(bad_answer(), RESPONSE, key=KEY) as server:
        monkeypatch.setenv("ANTHROPIC_BASE_URL", server.url)
        sut = make_sut(tmp_path, "one")
        summary = tmp_path / "summary.json"
        code, out, err = gt(capsys, *generate_args(sut, "--summary-json", summary, egress=tmp_path / "egress-one"))
        assert code == 0, out + err
        assert server.count == 2
    first, second = rows_of(egress_file(tmp_path, "one"))
    assert first["status"] == "bad_output" and (first["input_tokens"], first["output_tokens"]) == (1000, 77) and "status" not in second
    assert (second["input_tokens"], second["output_tokens"]) == (4210, 9350)
    data = json.loads(summary.read_text(encoding="utf-8"))
    assert data["usage"]["input_tokens"] == 1000 + 4210 and data["usage"]["output_tokens"] == 77 + 9350       # tổng cả hai lần: số tiền thật
    assert data["est_usd"] == pytest.approx(first["est_usd"] + second["est_usd"])


def test_when_both_answers_are_rejected_exit_3_still_leaves_both_rows(tmp_path, capsys, monkeypatch):
    with FakeAnthropic(bad_answer(77), bad_answer(55), key=KEY) as server:
        monkeypatch.setenv("ANTHROPIC_BASE_URL", server.url)
        sut = make_sut(tmp_path, "one")
        code, out, err = gt(capsys, *generate_args(sut, egress=tmp_path / "egress-one"))
    assert code == 3 and server.count == 2
    first, second = rows_of(egress_file(tmp_path, "one"))
    assert (first["output_tokens"], second["output_tokens"]) == (77, 55) and first["status"] == second["status"] == "bad_output"
    assert not (sut / GT / "test-cases.yaml").exists()                    # lỗi LLM không để lại file GT ghi dở


def test_a_timeout_is_an_unknown_cost_row_and_exit_3_still_writes_it(tmp_path, capsys, monkeypatch):
    def timeout(**kwargs):
        error = LLMError("timeout", "quá 300s", sent=True, unknown_calls=1)
        error.duration_s = 300.25
        raise error
    monkeypatch.setattr(gt_generate.llm, "call_tool", timeout)
    sut = make_sut(tmp_path, "one")
    summary = tmp_path / "summary.json"
    code, out, err = gt(capsys, *generate_args(sut, "--summary-json", summary, egress=tmp_path / "egress-one"))
    assert code == 3
    (row,) = rows_of(egress_file(tmp_path, "one"))
    assert row["status"] == "timeout" and row["usage_known"] is False and row["unknown_calls"] == 1 and row["duration_s"] == 300.25
    assert all(row[k] is None for k in ("input_tokens", "output_tokens", "cache_creation_input_tokens", "cache_read_input_tokens", "est_usd"))   # null, KHÔNG phải 0


def test_a_timeout_is_not_retried(tmp_path, capsys, monkeypatch):
    calls = []

    def timeout(**kwargs):
        calls.append(1)
        raise LLMError("timeout", "x", sent=True, unknown_calls=1)
    monkeypatch.setattr(gt_generate.llm, "call_tool", timeout)
    assert gt(capsys, *generate_args(make_sut(tmp_path, "one"), egress=tmp_path / "egress-one"))[0] == 3
    assert len(calls) == 1


@pytest.mark.parametrize("error", [LLMError("missing_key", "x"), LLMError("egress_denied", "x"), LLMError("unavailable", "HTTP 529", sent=True)])
def test_failures_that_cost_nothing_leave_no_usage_file(tmp_path, capsys, monkeypatch, error):
    monkeypatch.setattr(gt_generate.llm, "call_tool", lambda **kw: (_ for _ in ()).throw(error))
    assert gt(capsys, *generate_args(make_sut(tmp_path, "one"), egress=tmp_path / "egress-one"))[0] == 3
    assert not egress_file(tmp_path, "one").exists()


def test_a_cache_hit_row_keeps_the_original_numbers_but_costs_nothing_this_run(tmp_path, capsys, monkeypatch, fake):
    monkeypatch.setenv("QC_GT_CACHE_DIR", str(tmp_path / "gt-cache"))
    generated(tmp_path, capsys, "one")
    summary = tmp_path / "summary.json"
    sut = make_sut(tmp_path, "two")
    assert gt(capsys, *generate_args(sut, "--summary-json", summary, egress=tmp_path / "egress-two"))[0] == 0
    assert fake.count == 1
    (row,) = rows_of(egress_file(tmp_path, "two"))
    assert row["cache_hit"] is True and row["duration_s"] is None and (row["input_tokens"], row["output_tokens"]) == (4210, 9350)
    data = json.loads(summary.read_text(encoding="utf-8"))
    assert data["cache_hit"] is True and data["est_usd"] == 0.0 and data["unknown_calls"] == 0         # lần này không tốn gì


# ---------------- trần token ----------------

def test_over_the_cap_gt_exits_3_with_a_hint_to_split_the_prd_and_sends_nothing(tmp_path, capsys, monkeypatch, fake):
    monkeypatch.setenv("QC_LLM_MAX_INPUT_TOKENS", "100")
    sut = make_sut(tmp_path, "one")
    code, out, err = gt(capsys, *generate_args(sut, egress=tmp_path / "egress-one"))
    assert code == 3 and fake.count == 0
    assert "token_cap" in err and "QC_LLM_MAX_INPUT_TOKENS=100" in err and "tách PRD" in err and "ước lượng gần đúng" in err
    assert not (sut / GT / "test-cases.yaml").exists() and not egress_file(tmp_path, "one").exists()      # chưa gửi gì: không có dòng usage


def test_the_cap_applies_to_gemini_too_and_a_cache_hit_is_not_blocked(tmp_path, capsys, monkeypatch, fake):
    monkeypatch.setenv("QC_GT_CACHE_DIR", str(tmp_path / "gt-cache"))
    generated(tmp_path, capsys, "one")
    monkeypatch.setenv("QC_LLM_MAX_INPUT_TOKENS", "100")
    assert gt(capsys, *generate_args(make_sut(tmp_path, "two"), egress=tmp_path / "egress-two"))[0] == 0 and fake.count == 1     # hit: không tốn gì nên không bị chặn
    monkeypatch.setenv("QC_GT_MODEL", "gemini-3.6-flash")
    monkeypatch.setenv("GEMINI_API_KEY", "AIzaFAKE")
    monkeypatch.setattr(gt_generate.llm, "call_tool", lambda **kw: (_ for _ in ()).throw(AssertionError("không được gọi")))
    code, out, err = gt(capsys, *generate_args(make_sut(tmp_path, "three"), egress=tmp_path / "egress-three"))
    assert code == 3 and "token_cap" in err                                                          # cùng một ước lượng cho cả hai provider


def test_the_estimate_counts_bytes_so_a_cjk_prd_is_not_waved_through():
    chars = 2000
    prd_text = "漢" * chars
    assert client.estimate_input_tokens(prd_text) > -(-chars // 3) * 2


def test_the_default_cap_is_one_hundred_thousand_and_can_be_overridden(monkeypatch):
    from qc_agent import settings
    monkeypatch.delenv("QC_LLM_MAX_INPUT_TOKENS", raising=False)
    assert settings.get().llm_max_input_tokens == 100_000
    monkeypatch.setenv("QC_LLM_MAX_INPUT_TOKENS", "123")
    assert settings.get().llm_max_input_tokens == 123


# ---------------- GT agent: một dòng tổng, không áp trần ----------------

def test_the_agent_writes_one_total_row_with_turns_and_ignores_the_input_cap(tmp_path, capsys, monkeypatch, served):
    monkeypatch.setenv("QC_LLM_MAX_INPUT_TOKENS", "5")     # agent có ngân sách riêng (QC_GT_AGENT_MAX_*): trần này KHÔNG áp
    sut = make_sut(tmp_path, "one")
    (sut / "app").mkdir()
    (sut / "app" / "main.py").write_text("from fastapi import FastAPI\napp = FastAPI()\n", encoding="utf-8")
    summary = tmp_path / "summary.json"
    code, out, err = gt(capsys, *generate_args(sut, "--agent", "--summary-json", summary, egress=tmp_path / "egress-one"))
    assert code == 0, out + err
    (row,) = rows_of(egress_file(tmp_path, "one"))
    data = json.loads(summary.read_text(encoding="utf-8"))
    assert row["purpose"] == "gt-agent" and row["turns"] == served.count and row["cache_hit"] is False and "status" not in row
    assert row["input_tokens"] == data["usage"]["input_tokens"] and row["output_tokens"] == data["usage"]["output_tokens"]
    assert row["est_usd"] is not None and data["est_usd"] == pytest.approx(row["est_usd"])   # claude-sonnet-5-5 có trong bảng giá


def test_a_catalog_rejected_after_a_successful_call_still_reports_the_call_that_was_paid_for(tmp_path, capsys, monkeypatch, fake):
    monkeypatch.setattr(gt_generate.gt_schema, "validate_catalog", lambda catalog: ["lỗi lập trình giả"])
    code, out, err = gt(capsys, *generate_args(make_sut(tmp_path, "one"), egress=tmp_path / "egress-one"))
    assert code == 3 and fake.count == 1
    (row,) = rows_of(egress_file(tmp_path, "one"))
    assert (row["input_tokens"], row["output_tokens"]) == (4210, 9350)
