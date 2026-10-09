"""Kiểm kê event log `llm.* gt.* selector.* review.* jira.*` bằng AST (S4-07): tên và trường của mọi lời gọi `event(log, "<ns>.<tên>", ...)` trong `src/qc_agent`
phải khớp bảng ở docs/operations.md mục "Event log", và không trường nào có thể mang NỘI DUNG (prompt, diff, rationale, key, token, body của response...).
Không cần Docker, không chạy tiến trình: chỉ đọc mã nguồn và tài liệu. Test chuỗi đánh dấu (tests/test_log_no_content_chain.py) kiểm phía thực thi."""
import ast
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "src" / "qc_agent"
DOC = ROOT / "docs" / "operations.md"
NAMESPACES = ("llm.", "gt.", "selector.", "review.", "jira.")
# Tên trường KHÔNG được xuất hiện: chúng gợi nội dung hoặc bí mật. (`key` của event *.cache là tiền tố băm 8 ký tự, được kiểm riêng; `source` của selector.decision là enum.)
BANNED_FIELDS = {"prompt", "system", "user", "body", "text", "content", "diff", "patch", "hunks", "rationale", "response", "message", "secret", "token", "password",
                 "api_key", "authorization", "title", "detail", "error"}
# Hàm biến một giá trị tự do thành chuỗi: dùng trong giá trị của trường log là dấu hiệu nội dung bị nhét vào.
FREE_TEXT_CALLS = {"str", "format", "join", "repr", "json.dumps", "dumps"}
# Trường chứa dữ liệu do code khác dựng (không phải hằng) nhưng đã được kiểm bằng chuỗi đánh dấu: lý do ghi ở đây, không im lặng.
VETTED_DYNAMIC = {("jira.sync", "status"): "chỉ nhận hằng ('skipped: ...', 'error: HTTP <mã>', 'egress_denied', ...) hoặc tên lớp lỗi; marker JIRA_ERR trong body lỗi được kiểm ở test chuỗi"}
# `**stats` của gt.agent: chỉ các khoá này được chuyển tiếp (lọc ở groundtruth/agent.py).
GT_AGENT_STATS = {"turns", "stop", "completed", "files_read", "bytes_read", "submissions", "dropped_in_loop", "finish_rejections", "waivers", "spec_conflicts"}


def _calls() -> dict[str, list[dict]]:
    found: dict[str, list[dict]] = {}
    for path in sorted(SRC.rglob("*.py")):
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "event" and len(node.args) >= 2:
                arg = node.args[1]
                if isinstance(arg, ast.Constant) and isinstance(arg.value, str) and arg.value.startswith(NAMESPACES):
                    found.setdefault(arg.value, []).append({"where": f"{path.relative_to(ROOT).as_posix()}:{node.lineno}", "keywords": node.keywords})
    return found


CALLS = _calls()


def _documented() -> dict[str, set[str]]:
    """Bảng trong docs: `| `event` | khi nào | `trường`, `trường` |`."""
    section = DOC.read_text(encoding="utf-8").split("## Event log", 1)[1].split("\n## ", 1)[0]
    table = {}
    for line in section.splitlines():
        cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
        if len(cells) >= 3 and re.fullmatch(r"`[a-z]+\.[a-z_]+`", cells[0]):
            table[cells[0].strip("`")] = set(re.findall(r"`([a-z_]+)`", cells[2]))
    return table


def _fields(event_name: str) -> set[str]:
    return {kw.arg for call in CALLS[event_name] for kw in call["keywords"] if kw.arg}


def test_the_doc_lists_exactly_the_events_the_code_emits():
    assert CALLS, "AST không thấy event nào: bộ quét hỏng"
    documented = _documented()
    assert set(documented) == set(CALLS), {"thiếu trong docs": sorted(set(CALLS) - set(documented)), "docs thừa (code không phát)": sorted(set(documented) - set(CALLS))}


@pytest.mark.parametrize("name", sorted(CALLS))
def test_the_documented_fields_match_the_code(name):
    documented = _documented()[name]
    emitted = _fields(name)
    if any(kw.arg is None for call in CALLS[name] for kw in call["keywords"]):      # `**stats` (gt.agent)
        assert emitted <= documented and GT_AGENT_STATS <= documented, (sorted(emitted - documented), sorted(GT_AGENT_STATS - documented))
        assert documented <= emitted | GT_AGENT_STATS, sorted(documented - emitted - GT_AGENT_STATS)
    else:
        assert emitted == documented, {"code có, docs thiếu": sorted(emitted - documented), "docs có, code không": sorted(documented - emitted)}


def test_gt_agent_forwards_only_the_documented_stats_keys():
    text = (SRC / "groundtruth" / "agent.py").read_text(encoding="utf-8")
    match = re.search(r'if k in \(([^)]*)\)\}\)', text)
    assert match and set(re.findall(r'"([a-z_]+)"', match.group(1))) == GT_AGENT_STATS


@pytest.mark.parametrize("name", sorted(CALLS))
def test_no_field_name_can_carry_content(name):
    assert not _fields(name) & BANNED_FIELDS, sorted(_fields(name) & BANNED_FIELDS)


# Bọc số: giá trị ra là số/bool nên bên trong có `str(...)` cũng không đưa chuỗi tự do vào log (vd `int(str(x).startswith("error:"))`).
NUMERIC_WRAPPERS = {"int", "bool", "len", "round", "sum", "float"}


def _builds_free_text(node: ast.AST) -> bool:
    if isinstance(node, ast.JoinedStr):
        return True
    if isinstance(node, ast.Call):
        func = getattr(node.func, "id", None) or getattr(node.func, "attr", None)
        if func in NUMERIC_WRAPPERS:
            return False
        if func in FREE_TEXT_CALLS:
            return True
    if isinstance(node, ast.BinOp) and isinstance(node.op, (ast.Add, ast.Mod)):
        if any(isinstance(side, (ast.JoinedStr, ast.Constant)) and (isinstance(side, ast.JoinedStr) or isinstance(side.value, str)) for side in (node.left, node.right)):
            return True       # ghép chuỗi (`"error: " + x`, `"%s" % x`); `retry + 1` là phép cộng số nên không bị tính
    return any(_builds_free_text(child) for child in ast.iter_child_nodes(node))


@pytest.mark.parametrize("name", sorted(CALLS))
def test_no_field_value_interpolates_free_text(name):
    """Giá trị trường log không được dựng bằng f-string, ghép chuỗi, str()/format()/join(): đó là cách nội dung chui vào log."""
    for call in CALLS[name]:
        for kw in call["keywords"]:
            if (name, kw.arg) in VETTED_DYNAMIC:
                continue
            assert not _builds_free_text(kw.value), f"{call['where']}: trường {kw.arg} của {name} dựng chuỗi tự do ({ast.unparse(kw.value)})"


def test_the_free_text_detector_flags_what_it_should_and_spares_numbers():
    flags = lambda code: _builds_free_text(ast.parse(code, mode="eval").body)   # noqa: E731
    assert flags('f"x {y}"') and flags('"error: " + y') and flags('str(y)') and flags('" ".join(y)') and flags('"%s" % y') and flags('{"k": str(y)}')
    assert not flags("retry + 1") and not flags('int(str(y).startswith("a"))') and not flags("len(y)") and not flags('"hit" if y else "miss"') and not flags("round(x, 2)")


@pytest.mark.parametrize("name", ["gt.cache", "selector.cache"])
def test_the_cache_key_is_only_an_eight_character_hash_prefix(name):
    for call in CALLS[name]:
        for kw in call["keywords"]:
            if kw.arg == "key":
                assert isinstance(kw.value, ast.Subscript) and ast.unparse(kw.value.slice) == ":8", f"{call['where']}: key phải là băm cắt 8 ký tự"


@pytest.mark.parametrize("name", ["gt.cache", "selector.cache"])
def test_both_cache_events_share_the_outcome_field(name):
    """S4-07: `gt.cache skipped="agent"` đổi thành `outcome="skipped", reason="agent"` để jq lọc theo một khoá."""
    assert all("outcome" in {kw.arg for kw in call["keywords"]} for call in CALLS[name])
    assert "skipped" not in _fields(name)


def test_the_documented_vetted_dynamic_fields_still_exist():
    """Ngoại lệ không được trôi: nếu trường đã bị bỏ thì xoá khỏi VETTED_DYNAMIC."""
    for event_name, field in VETTED_DYNAMIC:
        assert field in _fields(event_name)


def test_the_jq_examples_in_the_doc_use_documented_event_names_and_fields():
    section = DOC.read_text(encoding="utf-8").split("## Event log", 1)[1].split("\n## ", 1)[0]
    documented = _documented()
    for match in re.finditer(r'select\(\.event==\\?"([a-z]+\.[a-z_]+)\\?"\)([^\n`]*)', section):
        assert match.group(1) in documented, match.group(0)
        for field in re.findall(r'\.([a-z_]+)', match.group(2)):
            assert field in documented[match.group(1)] | {"event"}, (match.group(0), field)
