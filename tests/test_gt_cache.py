"""Cache GT một-lời-gọi (S4-02): `gt generate|regen` gặp lại cùng PRD thì 0 lời gọi LLM mà file sinh ra giống từng byte (kể cả test-cases.xlsx).

LLM là `FakeAnthropic` (HTTP server giả, `ANTHROPIC_BASE_URL`) có đếm lời gọi. Agent KHÔNG được cache. Thư mục cache là thư mục tạm của test.
"""
import io
import json

import pytest

from qc_agent import logging_setup
from qc_agent.groundtruth import cache as gt_cache
from qc_agent.groundtruth import generate as gt_generate
from qc_agent.groundtruth.generate import generate
from qc_agent.groundtruth.prd import parse_prd
from qc_agent.llm.client import ToolCall, Usage
from tests.test_gt_agent import full_script, emitted, prd as agent_prd, spec  # noqa: F401  (fixtures + dựng script của agent)
from tests.test_gt_cli import GT, KEY, OPENAPI, PRD_FILE, RESPONSE, env, fake, generate_args, generated, gt, load_catalog, make_sut, tree  # noqa: F401
from tests.fakes import FakeAnthropic

MARKER = "PRIVATE_PRD_MARKER_5e1"


@pytest.fixture(autouse=True)
def cache_dir(monkeypatch, tmp_path):
    directory = tmp_path / "gt-cache"
    monkeypatch.setenv("QC_GT_CACHE_DIR", str(directory))
    return directory


def entries(directory):
    return sorted(directory.glob("*.json")) if directory.is_dir() else []


def parsed(path=PRD_FILE):
    return parse_prd(path, openapi_source=str(OPENAPI))


# ---------------- qua CLI ----------------

def test_second_generate_has_zero_calls_and_byte_identical_files(tmp_path, capsys, fake, cache_dir):
    first = generated(tmp_path, capsys, "one")
    assert fake.count == 1 and len(entries(cache_dir)) == 1
    summary = tmp_path / "summary.json"
    sut = make_sut(tmp_path, "two")
    code, out, err = gt(capsys, *generate_args(sut, "--summary-json", summary, egress=tmp_path / "egress-two"))
    assert code == 0, out + err
    assert fake.count == 1                                     # lần 2: không có request nào
    assert "cache: HIT" in out and json.loads(summary.read_text(encoding="utf-8"))["cache_hit"] is True
    a, b = tree(first), tree(sut)
    assert f"{GT}/test-cases.xlsx" in a and a == b             # giống từng byte, kể cả xlsx (mặc định được ghi)
    assert not (tmp_path / "egress-two" / "egress.jsonl").exists()   # không có gì rời máy


def test_regen_with_the_same_prd_is_a_hit(tmp_path, capsys, fake):
    sut = generated(tmp_path, capsys, "one")
    code, out, err = gt(capsys, "regen", "--prd", sut / "docs/prd/noteboard-prd.md", "--sut-root", sut, "--openapi", OPENAPI, "--egress-dir", tmp_path / "e")
    assert code == 0, out + err
    assert fake.count == 1 and "cache: HIT" in out


def test_changed_prd_is_a_miss(tmp_path, capsys, fake):
    generated(tmp_path, capsys, "one")
    generated(tmp_path, capsys, "two", prd_text=PRD_FILE.read_text(encoding="utf-8") + "\n")
    assert fake.count == 2


def test_agent_never_reads_or_writes_the_cache(tmp_path, capsys, monkeypatch, cache_dir, agent_prd, emitted):
    with FakeAnthropic(*full_script(agent_prd, emitted), *full_script(agent_prd, emitted), key=KEY) as server:
        monkeypatch.setenv("ANTHROPIC_BASE_URL", server.url)
        sut = make_sut(tmp_path)
        (sut / "app").mkdir()
        (sut / "app" / "main.py").write_text("from fastapi import FastAPI\napp = FastAPI()\n", encoding="utf-8")
        args = generate_args(sut, "--agent", egress=tmp_path / "egress")
        code, _, err_one = gt(capsys, *args)     # CLI tự cấu hình log ra stderr: đọc từ capsys
        assert code == 0
        after_first = server.count
        assert after_first > 1 and entries(cache_dir) == []
        code, _, err_two = gt(capsys, *args, "--force")
        assert code == 0 and server.count > after_first and entries(cache_dir) == []   # lần 2 vẫn gọi LLM
    skipped = [json.loads(line) for line in (err_one + err_two).splitlines() if '"gt.cache"' in line]
    assert [e["skipped"] for e in skipped] == ["agent", "agent"] and not any("outcome" in e for e in skipped)


def test_cache_can_be_disabled(tmp_path, capsys, monkeypatch, fake, cache_dir):
    monkeypatch.setenv("QC_GT_CACHE_DIR", "none")
    generated(tmp_path, capsys, "one")
    generated(tmp_path, capsys, "two")
    assert fake.count == 2 and entries(cache_dir) == []


# ---------------- qua generate() ----------------

def call(tmp_path, **kwargs):
    return generate(parsed(), model=kwargs.pop("model", "claude-sonnet-5"), egress_dir=tmp_path / "egress", **kwargs)


def test_catalog_from_a_hit_equals_the_catalog_from_the_call(tmp_path, fake, cache_dir):
    first = call(tmp_path)
    second = call(tmp_path)
    assert fake.count == 1 and not first.cache_hit and second.cache_hit
    assert second.catalog == first.catalog and second.warnings == first.warnings and second.orphans == first.orphans and second.dropped == first.dropped
    assert second.usage == first.usage                          # usage của lần sinh gốc


def test_changing_any_part_of_the_key_is_a_miss(tmp_path, monkeypatch, fake):
    call(tmp_path)
    assert call(tmp_path).cache_hit and fake.count == 1
    call(tmp_path, model="claude-other-model")
    assert fake.count == 2                                      # model
    version, system = gt_generate.load_prompt()
    with monkeypatch.context() as patch:
        patch.setattr(gt_generate, "load_prompt", lambda file=None: ("gt-generate/999", system))
        call(tmp_path)
    assert fake.count == 3                                      # prompt_version
    call(tmp_path, auth="<auth>\nstyle: bearer\n</auth>")
    assert fake.count == 4                                      # khối auth nằm trong prompt


def test_key_differs_by_generator_openapi_and_prd(tmp_path):
    base = dict(prd=parsed(), model="m", prompt_version="gt-generate/1", auth=None)
    key = gt_cache.make_key(**base)
    assert key != gt_cache.make_key(**base, generator="agent")
    assert key != gt_cache.make_key(**{**base, "prd": parse_prd(PRD_FILE)})                  # không có OpenAPI: endpoints khác
    assert key != gt_cache.make_key(**{**base, "model": "gemini-3-flash"})                    # provider nằm trong model
    assert key == gt_cache.make_key(**base)


def test_corrupt_or_invalid_entry_is_a_miss_not_an_error(tmp_path, fake, cache_dir):
    call(tmp_path)
    (path,) = entries(cache_dir)
    good = json.loads(path.read_text(encoding="utf-8"))
    for bad in ("{khong phai json", json.dumps([1, 2]), json.dumps({**good, "data": {"test_cases": "x"}}), json.dumps({**good, "version": 9}),
                json.dumps({**good, "usage": {"input_tokens": -1}}), json.dumps({**good, "attempts": 7})):
        path.write_text(bad, encoding="utf-8")
        before = fake.count
        result = call(tmp_path)
        assert not result.cache_hit and fake.count == before + 1 and result.catalog["test_cases"]     # miss, rồi entry được ghi lại hợp lệ
    assert call(tmp_path).cache_hit


def test_fallback_model_result_is_never_cached(tmp_path, monkeypatch, cache_dir):
    data = json.loads(RESPONSE.read_text(encoding="utf-8"))["content"][0]["input"]
    monkeypatch.setattr(gt_generate.llm, "call_tool", lambda **kw: ToolCall(data, Usage(1, 2), "gemini-fallback", "tool_use", 0.1, fallback_from="gemini-main"))
    assert call(tmp_path, model="gemini-main").catalog["generated_by"]["model"] == "gemini-fallback"
    assert entries(cache_dir) == []


def test_failed_call_is_never_cached(tmp_path, monkeypatch, cache_dir):
    with FakeAnthropic(529, key=KEY) as server:
        monkeypatch.setenv("ANTHROPIC_BASE_URL", server.url)
        with pytest.raises(gt_generate.GTError):
            call(tmp_path)
    assert entries(cache_dir) == []


def test_unwritable_cache_dir_does_not_break_generation(tmp_path, monkeypatch, fake):
    blocker = tmp_path / "file"
    blocker.write_text("x", encoding="utf-8")
    monkeypatch.setenv("QC_GT_CACHE_DIR", str(blocker / "sub"))   # mkdir dưới một file: lỗi
    assert call(tmp_path).catalog["test_cases"]


def test_cache_log_has_key_prefix_and_no_content(tmp_path, fake, cache_dir):
    text = PRD_FILE.read_text(encoding="utf-8")
    prd_file = tmp_path / "prd.md"
    prd_file.write_text(text + f"\n{MARKER}\n", encoding="utf-8")
    stream = io.StringIO()
    logging_setup.configure(stream)
    generate(parse_prd(prd_file, openapi_source=str(OPENAPI)), model="claude-sonnet-5", egress_dir=tmp_path / "e1")
    generate(parse_prd(prd_file, openapi_source=str(OPENAPI)), model="claude-sonnet-5", egress_dir=tmp_path / "e2")
    (path,) = entries(cache_dir)
    lines = [json.loads(line) for line in stream.getvalue().splitlines() if line.strip()]
    assert [(e["outcome"], e["key"]) for e in lines if e.get("event") == "gt.cache"] == [("miss", path.stem[:8]), ("store", path.stem[:8]), ("hit", path.stem[:8])]
    assert MARKER not in stream.getvalue() and path.stem not in stream.getvalue()
