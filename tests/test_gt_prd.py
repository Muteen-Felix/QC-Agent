"""groundtruth/prd.py + scaffold.openapi.endpoints (S1-02): parse PRD tất định, ID ổn định, không lộ nội dung PRD."""
import hashlib
import subprocess
import sys
from pathlib import Path

import pytest

from qc_agent.core import project
from qc_agent.groundtruth import prd
from qc_agent.groundtruth.prd import GTInputError, parse_prd
from qc_agent.scaffold import openapi

ROOT = Path(__file__).resolve().parent.parent
FIXTURE = ROOT / "tests" / "fixtures" / "prd" / "noteboard-prd.md"
NOTEBOARD_OPENAPI = ROOT / "tests" / "fixtures" / "openapi" / "noteboard.json"
VAHAN_OPENAPI = ROOT / "tests" / "fixtures" / "openapi" / "vahan-rpa.json"
MARK = "MARK-c07e19"


def write(tmp_path, name, content) -> Path:
    path = tmp_path / name
    path.write_bytes(content if isinstance(content, bytes) else content.encode("utf-8"))
    return path


def acs(parsed):
    return [(s.story_id, a.ac_id, a.text) for s in parsed.stories for a in s.acs]


# ---------------- PRD mẫu của noteboard ----------------

def test_sample_prd_has_the_documented_shape():
    parsed = parse_prd(FIXTURE)
    assert (parsed.prd_id, parsed.format, parsed.warnings, parsed.endpoints) == ("noteboard", "markdown", (), ())
    assert [(s.story_id, s.title) for s in parsed.stories] == [("US-1", "Tạo ghi chú"), ("US-2", "Xem ghi chú"), ("US-3", "Xoá ghi chú"), ("US-4", "Tóm tắt ghi chú")]
    ids = {s.story_id: [a.ac_id for a in s.acs] for s in parsed.stories}
    assert ids == {"US-1": [f"AC-1.{n}" for n in range(1, 9)], "US-2": [f"AC-2.{n}" for n in range(1, 7)],
                   "US-3": [f"AC-3.{n}" for n in range(1, 6)], "US-4": [f"AC-4.{n}" for n in range(1, 7)]}
    assert sum(len(v) for v in ids.values()) == 25
    assert all(a.text and "**" not in a.text and "\n" not in a.text for s in parsed.stories for a in s.acs)
    by_id = {a.ac_id: a.text for s in parsed.stories for a in s.acs}
    assert by_id["AC-1.1"].startswith("`POST /notes` với JSON") and "[[long]]" in by_id["AC-4.4"]
    assert "Hôm nay trời đẹp" in by_id["AC-4.2"]
    assert len(parsed.text) > 1000 and "US-1" in parsed.text


def test_sample_prd_with_its_openapi_lists_the_five_endpoints():
    parsed = parse_prd(FIXTURE, openapi_source=str(NOTEBOARD_OPENAPI))
    assert parsed.format == "markdown" and parsed.warnings == ()
    assert [(e["method"], e["path"]) for e in parsed.endpoints] == [
        ("GET", "/notes"), ("POST", "/notes"), ("GET", "/notes/{note_id}"), ("DELETE", "/notes/{note_id}"), ("POST", "/notes/{note_id}/summarize")]


def test_parsing_is_deterministic():
    assert parse_prd(FIXTURE, openapi_source=str(NOTEBOARD_OPENAPI)) == parse_prd(FIXTURE, openapi_source=str(NOTEBOARD_OPENAPI))


# ---------------- Markdown: nhận diện story và AC ----------------

VI = """# Tài liệu yêu cầu

## US-1: Tạo ghi chú
Mô tả câu chuyện, không phải AC.

### Tiêu chí chấp nhận
- AC-1.1: Tạo thành công trả 201.
- AC-1.2: Thiếu tiêu đề trả 422.
    - chi tiết lồng, sâu hơn: thuộc AC-1.2

## Câu chuyện người dùng: Xoá ghi chú

### Tiêu chí nghiệm thu
- Xoá xong thì GET trả 404.
* Xoá lần hai trả 404.
1. Xoá id lạ trả 404.
"""


def test_vietnamese_headings_bullets_and_nested_details(tmp_path):
    parsed = parse_prd(write(tmp_path, "vi.md", VI))
    assert [(s.story_id, len(s.acs)) for s in parsed.stories][0] == ("US-1", 2)
    first = parsed.stories[0].acs
    assert [a.ac_id for a in first] == ["AC-1.1", "AC-1.2"] and first[1].text == "Thiếu tiêu đề trả 422. chi tiết lồng, sâu hơn: thuộc AC-1.2"
    second = parsed.stories[1]
    assert second.title == "Xoá ghi chú" and second.story_id.startswith("US-") and len(second.acs) == 3
    assert all(a.ac_id.startswith("AC-") and len(a.ac_id) == 11 for a in second.acs)
    assert any("story" in w and "ID tường minh" in w for w in parsed.warnings) and any("3 AC không có ID" in w for w in parsed.warnings)


def test_given_when_then_in_all_three_layouts(tmp_path):
    text = """## US-1: Xoá
### Acceptance Criteria
- Given một ghi chú tồn tại
  - When tôi gọi DELETE
  - Then nhận 204
- Given ghi chú không tồn tại
- When tôi gọi DELETE
- Then nhận 404

Given id là chữ
When tôi gọi GET
And không có ghi chú
Then nhận 404

**AC-1.9** Given a, When b, Then c
"""
    parsed = parse_prd(write(tmp_path, "gwt.md", text))
    texts = [a.text for a in parsed.stories[0].acs]
    assert texts == ["Given một ghi chú tồn tại When tôi gọi DELETE Then nhận 204", "Given ghi chú không tồn tại When tôi gọi DELETE Then nhận 404",
                     "Given id là chữ When tôi gọi GET And không có ghi chú Then nhận 404", "Given a, When b, Then c"]
    assert parsed.stories[0].acs[-1].ac_id == "AC-1.9"


def test_explicit_ac_forms_and_normalisation(tmp_path):
    text = """## Story 2: Mẫu ID
AC-2.1: dạng thường
- **AC-2.2**: in đậm
- ac_2.3 - viết thường có gạch dưới
1. AC-2.10) đánh số
#### AC-2.11 dạng heading

mô tả nằm ở đoạn sau
"""
    parsed = parse_prd(write(tmp_path, "forms.md", text))
    assert parsed.stories[0].story_id == "US-2"      # "Story 2" -> US-2
    assert [(a.ac_id, a.text) for a in parsed.stories[0].acs] == [
        ("AC-2.1", "dạng thường"), ("AC-2.2", "in đậm"), ("AC-2.3", "viết thường có gạch dưới"), ("AC-2.10", "đánh số"), ("AC-2.11", "dạng heading mô tả nằm ở đoạn sau")]
    assert parsed.warnings == ()


def test_heading_style_ac_keeps_its_description_paragraphs_and_bullets_until_the_next_heading(tmp_path):
    text = "## US-1: A\n#### AC-1.1 Tạo ghi chú\n\nTrả 201.\n- có id\n- có title\n\n#### AC-1.2 Xoá\nTrả 204.\n### Ghi chú thêm\n- không thuộc AC nào\n"
    parsed = parse_prd(write(tmp_path, "h.md", text))
    assert [(a.ac_id, a.text) for a in parsed.stories[0].acs] == [("AC-1.1", "Tạo ghi chú Trả 201. có id có title"), ("AC-1.2", "Xoá Trả 204.")]


def test_story_line_form_and_ac_outside_any_story(tmp_path):
    text = "US-7: Là người dùng, tôi muốn xem danh sách.\n- AC-7.1: danh sách trả 200.\n\nAC-9.9: nằm ngoài\n"
    parsed = parse_prd(write(tmp_path, "line.md", text))
    assert [(s.story_id, [a.ac_id for a in s.acs]) for s in parsed.stories] == [("US-7", ["AC-7.1", "AC-9.9"])]
    outside = parse_prd(write(tmp_path, "out.md", "## Phạm vi\nAC-1.1: không có story nào\n"))
    assert [s.story_id for s in outside.stories] == ["S-1"] and outside.stories[0].acs[0].ac_id == "AC-1.1"
    assert any("ngoài mọi user story" in w for w in outside.warnings)


def test_code_fences_and_plain_headings_are_not_parsed_as_structure(tmp_path):
    text = """## US-1: Thật
### Acceptance Criteria
- AC-1.1: thật

```
## US-9: giả trong code
AC-9.9: giả trong code
```

## Phạm vi (US-1 đến US-3)
## Stories
- AC-8.8: nằm dưới heading không phải story, không phải mục AC
"""
    parsed = parse_prd(write(tmp_path, "fence.md", text))
    assert [(s.story_id, [a.ac_id for a in s.acs]) for s in parsed.stories] == [("US-1", ["AC-1.1"]), ("S-1", ["AC-8.8"])]


def test_story_without_acs_is_reported_and_kept(tmp_path):
    parsed = parse_prd(write(tmp_path, "empty.md", "## US-1: Chưa có AC\nchỉ có mô tả\n## US-2: Có\n- AC-2.1: x\n"))
    assert [(s.story_id, len(s.acs)) for s in parsed.stories] == [("US-1", 0), ("US-2", 1)] and "1 user story không có AC nào" in parsed.warnings


# ---------------- ID ----------------

UNKEYED = """## US-1: Tạo
### Acceptance Criteria
- Tạo thành công trả 201.
- Thiếu tiêu đề trả 422.
- Tiêu đề quá dài trả 422.
"""


def test_content_ids_are_stable_when_acs_are_reordered_or_lightly_reformatted(tmp_path):
    base = acs(parse_prd(write(tmp_path, "a.md", UNKEYED)))
    shuffled = "\n".join([UNKEYED.split("\n")[0], "### Acceptance Criteria", "- Tiêu đề quá dài trả 422.", "- Tạo thành công trả 201.", "- Thiếu tiêu đề trả 422.", ""])
    other = acs(parse_prd(write(tmp_path, "b.md", shuffled)))
    assert {a[1]: a[2] for a in base} == {a[1]: a[2] for a in other}   # cùng nội dung -> cùng ID, bất kể thứ tự
    spaced = UNKEYED.replace("Tạo thành công trả 201.", "  TẠO   thành công trả 201  ")   # khác khoảng trắng, hoa/thường, dấu chấm cuối
    assert {a[1] for a in acs(parse_prd(write(tmp_path, "c.md", spaced)))} == {a[1] for a in base}
    assert all(a[1].startswith("AC-") and len(a[1]) == 11 for a in base) and len({a[1] for a in base}) == 3


def test_content_ids_warn_once_and_reword_changes_the_id(tmp_path):
    parsed = parse_prd(write(tmp_path, "a.md", UNKEYED))
    assert [w for w in parsed.warnings if "ID tường minh" in w] == [
        "3 AC không có ID tường minh: ID sinh theo nội dung; nên ghi ID (AC-<n>) trong PRD để ID không đổi khi sửa chữ"]
    reworded = acs(parse_prd(write(tmp_path, "b.md", UNKEYED.replace("quá dài", "vượt giới hạn"))))
    assert {a[1] for a in reworded} != {a[1] for a in acs(parsed)}


def test_identical_unkeyed_acs_get_distinct_ids(tmp_path):
    parsed = parse_prd(write(tmp_path, "dup.md", "## US-1: X\n### AC\n- Trả 201.\n- Trả 201.\n"))
    ids = [a.ac_id for a in parsed.stories[0].acs]
    assert len(ids) == 2 and ids[0] != ids[1] and ids[1] == ids[0] + "-2"


@pytest.mark.parametrize("text,dup", [
    ("## US-1: A\n- AC-1.1: một\n## US-2: B\n- AC-1.1: hai\n", "AC-1.1"),
    ("## US-1: A\n### AC\n- AC-1.1: một\n## US-1: B\n", "US-1"),
])
def test_duplicate_explicit_ids_are_an_input_error_that_names_only_the_id(tmp_path, text, dup):
    with pytest.raises(GTInputError) as caught:
        parse_prd(write(tmp_path, "dup.md", text.replace("một", MARK).replace("hai", MARK)))
    assert dup in str(caught.value) and MARK not in str(caught.value)


# ---------------- front-matter và prd_id ----------------

def test_prd_id_comes_from_front_matter_then_file_name_and_is_a_slug(tmp_path):
    assert parse_prd(write(tmp_path, "x.md", "---\nid: Sổ Ghi Chú v2\n---\n## US-1: a\n- AC-1.1: b\n")).prd_id == "so-ghi-chu-v2"
    assert parse_prd(write(tmp_path, "Sổ tay ghi chú (bản 1).md", "## US-1: a\n- AC-1.1: b\n")).prd_id == "so-tay-ghi-chu-ban-1"
    assert parse_prd(write(tmp_path, "đường_ĐI.md", "x")).prd_id == "duong-di"
    assert parse_prd(write(tmp_path, "@@@.md", "x")).prd_id == "prd"
    long = parse_prd(write(tmp_path, "y.md", f"---\nid: {'a' * 200}\n---\nx")).prd_id
    assert len(long) <= 63 and set(long) == {"a"}


def test_front_matter_is_not_parsed_as_content_and_bad_yaml_only_warns(tmp_path):
    parsed = parse_prd(write(tmp_path, "fm.md", "---\nid: fm\ntitle: US-9 giả\n---\n## US-1: Thật\n- AC-1.1: x\n"))
    assert [s.story_id for s in parsed.stories] == ["US-1"] and parsed.warnings == ()
    bad = parse_prd(write(tmp_path, "bad.md", "---\nid: [chưa đóng\n---\n## US-1: Thật\n- AC-1.1: x\n"))
    assert bad.prd_id == "bad" and [s.story_id for s in bad.stories] == ["US-1"] and any("front-matter" in w for w in bad.warnings)
    assert parse_prd(write(tmp_path, "notfm.md", "--- không phải front-matter\n## US-1: a\n- AC-1.1: b\n")).stories[0].story_id == "US-1"


# ---------------- OpenAPI ----------------

def test_openapi_prd_lists_endpoints_and_has_no_stories():
    parsed = parse_prd(VAHAN_OPENAPI)
    assert (parsed.format, parsed.stories, parsed.prd_id) == ("openapi", (), "vahan-rpa")
    assert len(parsed.endpoints) == 15 and any("chỉ dùng làm danh sách endpoint" in w for w in parsed.warnings)
    keyed = {(e["method"], e["path"]): e for e in parsed.endpoints}
    assert ("GET", "/api/health") in keyed and ("PUT", "/api/ui-health/schedule") in keyed
    upload = keyed[("POST", "/api/jobs/{job_id}/upload-excel")]
    assert {"name": "job_id", "in": "path"} in upload["parameters"] and upload["body_required"] is True and "422" in upload["responses"]
    assert list(keyed) == sorted(keyed, key=lambda k: (k[1], ["GET", "POST", "PUT", "PATCH", "DELETE"].index(k[0])))   # xếp tất định


def test_openapi_source_is_ignored_with_a_warning_when_the_prd_is_already_openapi():
    parsed = parse_prd(VAHAN_OPENAPI, openapi_source=str(NOTEBOARD_OPENAPI))
    assert len(parsed.endpoints) == 15 and any("openapi_source bị bỏ qua" in w for w in parsed.warnings)


def test_yaml_openapi_is_detected_by_key_not_only_by_extension(tmp_path):
    spec = "openapi: 3.0.0\ninfo: {title: t, version: '1'}\npaths:\n  /a:\n    get:\n      responses: {'200': {description: ok}}\n"
    parsed = parse_prd(write(tmp_path, "api.yaml", spec))
    assert parsed.format == "openapi" and [(e["method"], e["path"]) for e in parsed.endpoints] == [("GET", "/a")]
    notapi = parse_prd(write(tmp_path, "cfg.yaml", "name: x\nitems: [1, 2]\n"))
    assert notapi.format == "text" and [s.story_id for s in notapi.stories] == ["S-1"]


def test_broken_openapi_source_is_an_input_error(tmp_path):
    md = write(tmp_path, "p.md", "## US-1: a\n- AC-1.1: b\n")
    for source in (str(tmp_path / "missing.json"), str(write(tmp_path, "notapi.json", '{"hello": 1}')), "ftp://x.test/o.json"):
        with pytest.raises(GTInputError):
            parse_prd(md, openapi_source=source)


def test_endpoints_from_swagger2_refs_and_ordering():
    spec = {
        "swagger": "2.0", "basePath": "/v1", "paths": {
            "/z": {"post": {"parameters": [{"in": "body", "name": "payload", "required": True}, {"$ref": "#/parameters/limit"}],
                            "responses": {"default": {}, "400": {}, "200": {}, "201": {}}}},
            "/a/{id}": {"parameters": [{"name": "trace", "in": "header", "required": False}],
                        "get": {"parameters": [{"name": "id", "in": "path"}, {"name": "q", "in": "query", "required": True}, {"name": "opt", "in": "query"}],
                                "responses": {"200": {}}},
                        "delete": {"responses": {"204": {}}}},
            "relative": {"get": {}}, "/skip": "not-an-object"},
        "parameters": {"limit": {"name": "limit", "in": "query", "required": True}},
    }
    assert openapi.endpoints(spec) == [
        {"method": "GET", "path": "/v1/a/{id}", "spec_path": "/a/{id}", "parameters": [{"name": "id", "in": "path"}, {"name": "q", "in": "query"}],
         "body_required": False, "responses": ["200"]},
        {"method": "DELETE", "path": "/v1/a/{id}", "spec_path": "/a/{id}", "parameters": [], "body_required": False, "responses": ["204"]},
        {"method": "POST", "path": "/v1/z", "spec_path": "/z", "parameters": [{"name": "limit", "in": "query"}], "body_required": True,
         "responses": ["200", "201", "400", "default"]},
    ]


def test_endpoints_openapi3_request_body_and_unresolvable_refs():
    spec = {"openapi": "3.1.0", "paths": {
        "/n": {"post": {"requestBody": {"required": True, "content": {}}, "responses": {"201": {}}},
               "put": {"requestBody": {"content": {}}, "responses": {"200": {}}},
               "get": {"parameters": [{"$ref": "#/components/parameters/nope"}, {"$ref": "https://x.test/p.json"}], "responses": {"200": {}}}}}}
    got = {e["method"]: e for e in openapi.endpoints(spec)}
    assert got["POST"]["body_required"] is True and got["PUT"]["body_required"] is False and got["GET"]["parameters"] == []   # không đoán tham số không giải được


# ---------------- text thô ----------------

def test_plain_text_falls_back_to_one_story_without_acs(tmp_path):
    parsed = parse_prd(write(tmp_path, "yeu-cau.txt", "Hệ thống cần cho phép tạo ghi chú.\nVà xoá ghi chú.\n"))
    assert (parsed.format, parsed.prd_id) == ("text", "yeu-cau")
    assert [(s.story_id, s.title, s.acs) for s in parsed.stories] == [("S-1", "Hệ thống cần cho phép tạo ghi chú.", ())]
    assert any("không nhận ra user story/AC nào" in w for w in parsed.warnings)
    assert "Và xoá ghi chú." in parsed.text


def test_markdown_without_recognisable_structure_also_falls_back(tmp_path):
    parsed = parse_prd(write(tmp_path, "free.md", "# Tiêu đề chung\nvài dòng mô tả tự do\n"))
    assert parsed.format == "markdown" and [s.story_id for s in parsed.stories] == ["S-1"] and parsed.stories[0].title == "Tiêu đề chung"
    assert parse_prd(write(tmp_path, "empty.txt", "")).stories[0].title == "empty"   # tệp rỗng: lấy prd_id làm tiêu đề


# ---------------- sha256 ----------------

def test_sha256_ignores_crlf_and_bom_and_matches_file_sha256_for_plain_utf8(tmp_path):
    lf = FIXTURE.read_bytes().replace(b"\r\n", b"\n")
    crlf = write(tmp_path, "crlf.md", lf.replace(b"\n", b"\r\n"))
    bom = write(tmp_path, "bom.md", b"\xef\xbb\xbf" + lf)
    plain = write(tmp_path, "plain.md", lf)
    expected = hashlib.sha256(lf).hexdigest()
    assert parse_prd(plain).sha256 == parse_prd(crlf).sha256 == parse_prd(bom).sha256 == expected
    assert project.file_sha256(crlf) == parse_prd(crlf).sha256                      # "giống core/project.file_sha256"
    assert project.file_sha256(bom) != parse_prd(bom).sha256                       # chỉ khác ở chỗ bỏ BOM
    assert parse_prd(write(tmp_path, "other.md", lf + "\nthêm".encode("utf-8"))).sha256 != expected
    assert acs(parse_prd(crlf)) == acs(parse_prd(plain)) == acs(parse_prd(bom))
    assert "\r" not in parse_prd(crlf).text and not parse_prd(bom).text.startswith("\ufeff")


# ---------------- giới hạn và lỗi đầu vào ----------------

def test_size_limit_is_256_kb(tmp_path):
    head = b"## US-1: a\n- AC-1.1: "
    ok = write(tmp_path, "ok.md", head + b"x" * (prd.MAX_BYTES - len(head)))
    assert ok.stat().st_size == prd.MAX_BYTES and parse_prd(ok).stories[0].story_id == "US-1"
    too_big = write(tmp_path, "big.md", head + MARK.encode() * 40 + b"x" * prd.MAX_BYTES)
    with pytest.raises(GTInputError) as caught:
        parse_prd(too_big)
    assert str(prd.MAX_BYTES) in str(caught.value) and MARK not in str(caught.value)


def test_unreadable_or_non_utf8_input_is_an_input_error(tmp_path):
    for target in (tmp_path / "missing.md", tmp_path):
        with pytest.raises(GTInputError):
            parse_prd(target)
    with pytest.raises(GTInputError, match="UTF-8"):
        parse_prd(write(tmp_path, "latin1.md", "Tiêu đề".encode("utf-16")))


# ---------------- không lộ nội dung PRD ----------------

def test_no_prd_text_in_errors_warnings_or_repr(tmp_path):
    text = f"---\nid: {MARK}\n---\n## US-1: Tiêu đề {MARK}\n### AC\n- AC-1.1: câu {MARK}\n- Không ID {MARK}\nGiven {MARK} nằm ngoài\n"
    parsed = parse_prd(write(tmp_path, "leak.md", text))
    assert MARK not in " ".join(parsed.warnings)
    assert parsed.prd_id == "mark-c07e19"                                          # prd_id là slug do người đặt (front-matter), được phép hiện
    dumped = repr(parsed) + repr(parsed.stories) + repr(parsed.endpoints)
    assert MARK not in dumped and "AC-1.1" in dumped                               # repr chỉ có ID, không có văn bản
    assert MARK in parsed.text and MARK in parsed.stories[0].title                 # văn bản vẫn truy cập được, chỉ không tự lọt vào repr


def test_module_does_not_import_logging():
    import ast
    tree = ast.parse((ROOT / "src" / "qc_agent" / "groundtruth" / "prd.py").read_text(encoding="utf-8"))
    imported = {alias.name for node in ast.walk(tree) if isinstance(node, ast.Import) for alias in node.names}
    imported |= {node.module or "" for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)}
    assert not [name for name in imported if "logging" in name]


# ---------------- core/ không kéo groundtruth vào ----------------

def test_importing_core_cli_and_engine_never_pulls_groundtruth_in():
    code = ("import sys; import qc_agent.core.cli, qc_agent.core.engine; "
            "bad = sorted(m for m in sys.modules if m == 'qc_agent.groundtruth' or m.startswith('qc_agent.groundtruth.')); "
            "print(bad); sys.exit(1 if bad else 0)")
    done = subprocess.run([sys.executable, "-c", code], cwd=ROOT, capture_output=True, text=True, encoding="utf-8", timeout=120)
    assert done.returncode == 0, done.stdout + done.stderr
