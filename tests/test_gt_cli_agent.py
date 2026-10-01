"""`qc-agent gt generate|regen --agent`: agent chạy qua CLI thật (core.cli.main), LLM là FakeAnthropic (HTTP server giả) phát hội thoại nhiều lượt soạn sẵn.

Bằng chứng quan trọng nhất: bộ TC của agent, SAU KHI QA duyệt hết, đi qua cổng `gt validate` nghiêm ngặt (coverage AC + technique + API đều 100%, không cần nới policy).
"""
import json
from pathlib import Path

import pytest
import yaml

from qc_agent.groundtruth import pr_body
from tests.fakes import FakeAnthropic
from tests.test_gt_agent import full_script, emitted, prd, spec  # noqa: F401  (fixtures + dựng script)
from tests.test_gt_cli import GT, KEY, OPENAPI, approve_everything, env, generate_args, gt, load_catalog, make_sut, save_catalog  # noqa: F401


@pytest.fixture
def served(monkeypatch, prd, emitted):
    """FakeAnthropic phát full_script HAI lần liền nhau (server giả đếm theo chỉ số request trong cả đời nó, không theo từng lần chạy), để test có
    thể chạy `generate` rồi `regen`. Test chỉ chạy một lần thì không dùng tới nửa sau."""
    with FakeAnthropic(*full_script(prd, emitted), *full_script(prd, emitted), key=KEY) as server:
        monkeypatch.setenv("ANTHROPIC_BASE_URL", server.url)
        yield server


def sut_with_source(tmp_path, name="sut"):
    sut = make_sut(tmp_path, name)
    (sut / "app").mkdir()
    (sut / "app" / "main.py").write_text("from fastapi import FastAPI\napp = FastAPI()\n", encoding="utf-8")
    return sut


def run_generate(capsys, sut, tmp_path, *extra, **kwargs):
    summary = tmp_path / "summary.json"
    code, out, err = gt(capsys, *generate_args(sut, "--summary-json", summary, *extra, egress=tmp_path / "egress", **kwargs))
    return code, out, err, (json.loads(summary.read_text(encoding="utf-8")) if summary.exists() else None)


def test_agent_generate_summary_catalog_and_the_strict_gate_after_qa_approval(tmp_path, capsys, served):
    sut = sut_with_source(tmp_path)
    code, out, err, summary = run_generate(capsys, sut, tmp_path, "--agent")
    assert code == 0, out + err
    assert summary["generator"] == "agent" and summary["model"] == "claude-sonnet-5-5" and summary["prompt_version"] == "gt-agent/1"
    agent = summary["agent"]
    assert agent["completed"] is True and agent["stop"] == "finished" and agent["files_read"] == 1 and agent["finish_rejections"] == 1 and agent["turns"] == served.count
    assert {k: (v["covered"] + v["waived"], v["total"]) for k, v in summary["coverage"].items()} == {"ac": (25, 25), "technique": (10, 10), "api": (12, 12)}
    assert summary["by_status"]["approved"] == 0 and summary["orphans"] == [] and summary["dropped_test_cases"] == 5
    assert "Bộ sinh: AGENT" in out and "hoàn tất: có" in out and "coverage api: 12/12 (100%)" in out
    catalog = load_catalog(sut)
    assert catalog["generated_by"]["prompt_version"] == "gt-agent/1" and {tc["status"] for tc in catalog["test_cases"]} == {"draft"}
    assert any(tc.get("technique") == "error_handling" and tc.get("priority") == "medium" for tc in catalog["test_cases"])
    assert (sut / GT / "openapi.snapshot.json").is_file() and (sut / GT / "tests_gt" / "conftest.py").is_file()

    # QA duyệt hết (không nới policy): cổng nghiêm ngặt phải xanh vì agent đã đủ 100%.
    approve_everything(sut)                                   # QA sửa YAML trực tiếp: xlsx thành cũ (chỉ cảnh báo, không chặn)
    (sut / GT / "coverage-policy.yaml").unlink()
    code, out, _ = gt(capsys, "validate", "--sut-root", sut)
    assert code == 0 and "coverage" not in out and "Excel đã cũ" in out, out
    assert gt(capsys, "export-xlsx", "--sut-root", sut)[0] == 0   # xuất lại cho khớp
    code, out, _ = gt(capsys, "validate", "--sut-root", sut)
    assert code == 0 and "0 lỗi, 0 cảnh báo" in out, out


def test_the_agent_run_sent_the_right_requests(tmp_path, capsys, served):
    code, out, err, _ = run_generate(capsys, sut_with_source(tmp_path), tmp_path, "--agent")
    assert code == 0, err
    first = served.requests[0]
    assert first["headers"]["x-api-key"] == KEY and first["body"]["tool_choice"] == {"type": "auto"} and first["body"]["model"] == "claude-sonnet-5-5"
    assert not any(r["rejected"] for r in served.requests)
    text = first["body"]["messages"][0]["content"][0]["text"]
    assert "<repo_overview>" in text and "app/" in text and "<prd>" in text and "<endpoints>" in text
    lines = [json.loads(l) for l in (tmp_path / "egress" / "egress.jsonl").read_text(encoding="utf-8").splitlines()]
    assert len(lines) == served.count and {tuple(l["categories"]) for l in lines} == {("api_spec", "prd_text", "source_code")}
    assert str(tmp_path / "egress") not in json.dumps(summary_text(tmp_path))


def summary_text(tmp_path):
    return json.loads((tmp_path / "summary.json").read_text(encoding="utf-8"))


def test_generator_selection_flag_env_and_conflicts(tmp_path, capsys, served, monkeypatch):
    sut = sut_with_source(tmp_path)
    assert run_generate(capsys, sut, tmp_path, "--agent", "--no-agent")[0] == 3
    monkeypatch.setenv("QC_GT_GENERATOR", "bogus")
    code, out, err, _ = run_generate(capsys, sut, tmp_path)
    assert code == 3 and "QC_GT_GENERATOR" in err and served.count == 0
    monkeypatch.setenv("QC_GT_GENERATOR", "agent")
    code, out, err, summary = run_generate(capsys, sut, tmp_path)           # chỉ bằng biến môi trường, không có cờ
    assert code == 0 and summary["generator"] == "agent", err


def test_no_agent_forces_the_single_shot_generator_even_when_the_env_says_agent(tmp_path, capsys, served, monkeypatch):
    monkeypatch.setenv("QC_GT_GENERATOR", "agent")
    code, out, err, _ = run_generate(capsys, sut_with_source(tmp_path), tmp_path, "--no-agent")
    assert served.requests[0]["body"]["tool_choice"] == {"type": "tool", "name": "emit_test_cases"}   # đường một lời gọi: ép tool
    assert code == 3                                                         # server giả chỉ biết nói chuyện của agent nên single-shot báo bad_output


def test_a_missing_source_root_is_a_clean_error_and_nothing_is_sent(tmp_path, capsys, served):
    code, out, err, _ = run_generate(capsys, sut_with_source(tmp_path), tmp_path, "--agent", "--source-root", tmp_path / "nope")
    assert code == 3 and "--source-root không phải thư mục" in err and served.count == 0


def test_source_root_can_point_at_another_checkout(tmp_path, capsys, served):
    sut = make_sut(tmp_path)                                                   # SUT không có mã nguồn; mã nằm ở checkout riêng (CI: origin/<base> mount :ro)
    checkout = tmp_path / "checkout"
    (checkout / "svc").mkdir(parents=True)
    (checkout / "svc" / "main.py").write_text("x = 1\n", encoding="utf-8")
    code, out, err, summary = run_generate(capsys, sut, tmp_path, "--agent", "--source-root", checkout)
    assert code == 0, err
    assert "svc/" in served.requests[0]["body"]["messages"][0]["content"][0]["text"] and not (sut / "svc").exists()


def test_agent_without_openapi_still_works_but_only_scores_acs(tmp_path, capsys, served):
    sut = sut_with_source(tmp_path)
    code, out, err, summary = run_generate(capsys, sut, tmp_path, "--agent", openapi=False)
    assert code == 0, err
    assert list(summary["coverage"]) == ["ac"] and any("agent chạy không có --openapi" in w for w in summary["warnings"])
    assert not (sut / GT / "openapi.snapshot.json").exists()
    assert "openapi_operation" not in [t["name"] for t in served.requests[0]["body"]["tools"]]


def test_regen_with_the_agent_keeps_qa_decisions_and_tells_the_agent_about_them(tmp_path, capsys, served):
    sut = sut_with_source(tmp_path)
    assert run_generate(capsys, sut, tmp_path, "--agent")[0] == 0
    catalog = load_catalog(sut)
    keep, reject = catalog["test_cases"][0], catalog["test_cases"][1]
    keep.update(status="approved", notes="QA ghi chú")
    reject.update(status="rejected", rejected_reason="sai PRD")
    save_catalog(sut, catalog)
    before = served.count
    code, out, err = gt(capsys, "regen", *generate_args(sut, "--agent", egress=tmp_path / "egress2")[1:])
    assert code == 0, out + err
    after = load_catalog(sut)
    by_id = {tc["tc_id"]: tc for tc in after["test_cases"]}
    assert by_id[keep["tc_id"]] == keep and by_id[reject["tc_id"]] == reject                      # nguyên văn
    text = served.requests[before]["body"]["messages"][0]["content"][0]["text"]
    assert "<existing_cases>" in text and keep["tc_id"] in text and reject["tc_id"] in text
    assert {tc["status"] for tc in after["test_cases"]} >= {"approved", "rejected", "draft"}


def test_an_incomplete_agent_run_still_writes_a_draft_and_says_so(tmp_path, capsys, monkeypatch, prd, emitted, served):
    monkeypatch.setenv("QC_GT_AGENT_MAX_TURNS", "3")
    sut = sut_with_source(tmp_path)
    code, out, err, summary = run_generate(capsys, sut, tmp_path, "--agent")
    assert code == 0, err                                                       # bộ dở vẫn được ghi để QA xem: PR cảnh báo chứ không mất sạch
    assert summary["agent"]["completed"] is False and summary["agent"]["stop"] == "budget_turns" and summary["agent"]["turns"] == 3
    assert any("hết ngân sách" in w for w in summary["warnings"]) and "KHÔNG (xem coverage và cảnh báo)" in out
    assert load_catalog(sut)["test_cases"]
    body = pr_body.render(summary)
    assert "Bộ sinh: **agent**" in body and "chưa hoàn tất" in body and "budget\\_turns" in body


def test_pr_body_for_an_agent_summary_lists_the_new_qa_duties(tmp_path, capsys, served):
    code, out, err, summary = run_generate(capsys, sut_with_source(tmp_path), tmp_path, "--agent")
    assert code == 0, err
    summary["agent"].update(waivers=3, spec_conflicts=2, cost_usd_est=1.5)
    body = pr_body.render(summary)
    assert "Bộ sinh: **agent** ·" in body and "~$1.50" in body and "chưa hoàn tất" not in body
    assert "Duyệt **3** waiver" in body and "Quyết **2** xung đột spec" in body and "### Coverage" in body
    legacy = {k: v for k, v in summary.items() if k not in ("agent", "generator")}
    assert "Bộ sinh" not in pr_body.render(legacy)                              # summary của bản cũ vẫn dựng được


def test_the_summary_and_stdout_contain_no_source_content(tmp_path, capsys, served):
    sut = sut_with_source(tmp_path)
    (sut / "app" / "main.py").write_text("# SOURCE-SENTINEL-do-not-print\n", encoding="utf-8")
    code, out, err, summary = run_generate(capsys, sut, tmp_path, "--agent")
    assert code == 0
    assert "SOURCE-SENTINEL" not in out + err + json.dumps(summary, ensure_ascii=False)
