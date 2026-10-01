"""groundtruth/coverage.py: bộ chấm coverage tất định (AC, technique, API) + cổng của `gt validate`.

Phần 1 dùng OpenAPI tổng hợp (đủ loại ràng buộc) và catalog dựng tay để kiểm TỪNG luật và các cách "ăn gian".
Phần 2 chạy CLI thật trên noteboard (FakeAnthropic) để kiểm baseline của bộ catalog single-shot hiện tại và hành vi của `gt validate`.
"""
import copy
import json

import pytest
import yaml

from qc_agent.groundtruth import coverage as cov
from qc_agent.groundtruth import pr_body
from tests.test_gt_cli import GT, OPENAPI, approve_everything, env, fake, generate_args, gt, generated, load_catalog, make_sut, save_catalog  # noqa: F401

SPEC = {
    "openapi": "3.1.0", "info": {"title": "t", "version": "1"},
    "components": {"schemas": {"ItemIn": {"type": "object", "required": ["name", "qty"], "properties": {
        "name": {"type": "string", "minLength": 2, "maxLength": 10, "pattern": "^[a-z]+$"},
        "qty": {"type": "integer", "minimum": 1, "maximum": 99},
        "kind": {"anyOf": [{"type": "string", "enum": ["a", "b"]}, {"type": "null"}]},
        "price": {"type": "number", "exclusiveMinimum": 0},
    }}}},
    "paths": {"/items": {
        "post": {"security": [{"k": []}], "requestBody": {"required": True, "content": {"application/json": {"schema": {"$ref": "#/components/schemas/ItemIn"}}}},
                 "responses": {"201": {"description": "x"}, "422": {"description": "x"}, "500": {"description": "x"}, "default": {"description": "x"}}},
        "get": {"parameters": [{"name": "limit", "in": "query", "required": False, "schema": {"type": "integer", "minimum": 1, "maximum": 50}}],
                "responses": {"200": {"description": "x"}}},
    }},
}
FACTS = cov.snapshot(SPEC)
POST = next(op for op in FACTS["operations"] if op["method"] == "POST")
GET = next(op for op in FACTS["operations"] if op["method"] == "GET")


def step(method="POST", path="/items", status=(201,), json_body=None, query=None):
    request = {"method": method, "path": path}
    if json_body is not None:
        request["json"] = json_body
    if query is not None:
        request["query"] = query
    return {"request": request, "expect": {"status": list(status)}}


def tc(*steps, tc_id="TC-AC-1-aaaaaa", ac="AC-1", status="approved", **extra):
    return {"tc_id": tc_id, "title": "t", "ac_refs": [ac], "kind": "api_functional", "status": status, "origin": "llm", "steps": list(steps), **extra}


def catalog(*cases, uncovered=(), waivers=()):
    out = {"version": 1, "stories": [{"story_id": "US-1", "title": "s", "acs": [{"ac_id": "AC-1", "text": "a"}]}],
           "test_cases": list(cases), "uncovered_acs": [{"ac_id": a, "reason": "r"} for a in uncovered]}
    if waivers:
        out["waivers"] = list(waivers)
    return out


def gaps(report, dim):
    return set(getattr(report, dim).gaps)


def covered_by(*steps, dim="technique", **kwargs):
    """Gap còn lại sau khi chấm một TC chứa `steps` (chỉ tính gap của chiều `dim`)."""
    return gaps(cov.score(catalog(tc(*steps)), FACTS, **kwargs), dim)


# ---------------- snapshot ----------------

def test_snapshot_extracts_constraints_from_refs_anyof_and_exclusive_bounds():
    fields = {f["name"]: f for f in POST["body"]["fields"]}
    assert fields["name"] == {"name": "name", "required": True, "type": "string", "maxLength": 10, "minLength": 2, "pattern": "^[a-z]+$"}
    assert fields["qty"]["minimum"] == 1 and fields["qty"]["maximum"] == 99 and fields["qty"]["required"] is True
    assert fields["kind"]["enum"] == ["a", "b"] and fields["kind"]["required"] is False   # anyOf [x, null] -> nhánh không-null
    assert fields["price"]["minimum"] == 0 and fields["price"]["exclusiveMinimum"] is True
    assert POST["secured"] is True and GET["secured"] is False
    assert GET["params"] == [{"name": "limit", "in": "query", "required": False, "type": "integer", "minimum": 1, "maximum": 50}]


def test_snapshot_is_deterministic_and_has_no_descriptions_or_servers():
    spec = copy.deepcopy(SPEC)
    spec["servers"] = [{"url": "https://secret.internal.example/api"}]
    spec["paths"]["/items"]["post"]["description"] = "mô tả nội bộ"
    first, second = cov.snapshot_text(cov.snapshot(spec)), cov.snapshot_text(cov.snapshot(copy.deepcopy(spec)))
    assert first == second and "secret.internal" not in first and "mô tả" not in first and first.endswith("\n")


def test_snapshot_survives_self_referencing_and_dangling_refs():
    spec = copy.deepcopy(SPEC)
    spec["components"]["schemas"]["Loop"] = {"type": "object", "properties": {"me": {"$ref": "#/components/schemas/Loop"}}}
    spec["paths"]["/loop"] = {"post": {"requestBody": {"content": {"application/json": {"schema": {"$ref": "#/components/schemas/Loop"}}}},
                               "responses": {"200": {"description": "x"}}},
                              "put": {"requestBody": {"content": {"application/json": {"schema": {"$ref": "#/components/schemas/Missing"}}}},
                                      "responses": {"200": {"description": "x"}}}}
    ops = {(o["method"], o["path"]): o for o in cov.snapshot(spec)["operations"]}
    assert ops[("POST", "/loop")]["body"]["fields"][0]["name"] == "me" and ops[("PUT", "/loop")]["body"]["fields"] == []


# ---------------- yêu cầu suy ra ----------------

def test_requirements_are_derived_only_from_what_the_spec_declares():
    assert [r.id for r in cov.requirements(POST)] == [
        "equivalence:enum:body.kind",
        "negative_validation:missing:body.name", "boundary:max_length:body.name@10", "boundary:max_length:body.name@11",
        "boundary:min_length:body.name@2", "boundary:min_length:body.name@1", "equivalence:pattern:body.name",
        "boundary:minimum:body.price@0",
        "negative_validation:missing:body.qty", "boundary:maximum:body.qty@99", "boundary:maximum:body.qty@100",
        "boundary:minimum:body.qty@1", "boundary:minimum:body.qty@0",
        "authz:unauthenticated"]
    assert [r.id for r in cov.requirements(GET)] == ["boundary:maximum:query.limit@50", "boundary:maximum:query.limit@51",
                                                     "boundary:minimum:query.limit@1", "boundary:minimum:query.limit@0"]


def test_boundaries_too_large_to_test_and_exclusive_integer_bounds():
    op = {"method": "POST", "path": "/x", "secured": False, "responses": [], "params": [],
          "body": {"required": True, "fields": [{"name": "blob", "required": False, "type": "string", "maxLength": 1_000_000},
                                                {"name": "n", "required": False, "type": "integer", "maximum": 10, "exclusiveMaximum": True}]}}
    assert [r.id for r in cov.requirements(op)] == ["boundary:maximum:body.n@9", "boundary:maximum:body.n@10"]   # biên mở: 9 hợp lệ, 10 bị từ chối


# ---------------- TC phủ yêu cầu: luật và cách ăn gian ----------------

VALID = {"name": "abc", "qty": 5}


@pytest.mark.parametrize("body,status,requirement", [
    ({"qty": 5}, (422,), "negative_validation:missing:body.name"),
    ({"name": "abc"}, (400, 422), "negative_validation:missing:body.qty"),
    ({**VALID, "name": "a" * 10}, (201,), "boundary:max_length:body.name@10"),
    ({**VALID, "name": "a" * 11}, (422,), "boundary:max_length:body.name@11"),
    ({**VALID, "name": "ab"}, (201,), "boundary:min_length:body.name@2"),
    ({**VALID, "name": "a"}, (422,), "boundary:min_length:body.name@1"),
    ({**VALID, "name": "ABC"}, (422,), "equivalence:pattern:body.name"),
    ({**VALID, "qty": 99}, (201,), "boundary:maximum:body.qty@99"),
    ({**VALID, "qty": 100}, (422,), "boundary:maximum:body.qty@100"),
    ({**VALID, "qty": 1}, (201,), "boundary:minimum:body.qty@1"),
    ({**VALID, "qty": 0}, (422,), "boundary:minimum:body.qty@0"),
    ({**VALID, "kind": "zzz"}, (422,), "equivalence:enum:body.kind"),
    ({**VALID, "price": 0}, (422,), "boundary:minimum:body.price@0"),     # biên mở: chính 0 bị từ chối
    ({**VALID, "price": -3.5}, (422,), "boundary:minimum:body.price@0"),
])
def test_a_step_that_really_does_the_thing_covers_the_requirement(body, status, requirement):
    assert f"POST /items {requirement}" not in covered_by(step(json_body=body, status=status))


@pytest.mark.parametrize("body,status,requirement", [
    ({"qty": 5, "extra": 1}, (201,), "negative_validation:missing:body.name"),          # thiếu field nhưng mong 2xx
    ({}, (422,), "negative_validation:missing:body.name"),                              # thiếu CẢ HAI: không quy được lỗi về một field
    ({**VALID, "name": "a" * 9}, (201,), "boundary:max_length:body.name@10"),           # sai biên một đơn vị
    ({**VALID, "name": "a" * 12}, (422,), "boundary:max_length:body.name@11"),
    ({**VALID, "name": "a" * 10}, (422,), "boundary:max_length:body.name@10"),          # đúng độ dài nhưng mong sai mã
    ({**VALID, "name": "a" * 11}, (201,), "boundary:max_length:body.name@11"),
    ({**VALID, "qty": 98}, (201,), "boundary:maximum:body.qty@99"),
    ({**VALID, "qty": 101}, (422,), "boundary:maximum:body.qty@100"),
    ({**VALID, "qty": True}, (201,), "boundary:minimum:body.qty@1"),                    # bool không phải số
    ({**VALID, "name": "{{made_up}}"}, (422,), "equivalence:pattern:body.name"),        # biến capture: chưa biết giá trị
    ({**VALID, "kind": "a"}, (422,), "equivalence:enum:body.kind"),                     # giá trị TRONG enum
    ({**VALID, "name": "abc"}, (422,), "equivalence:pattern:body.name"),                # khớp pattern
    ({**VALID, "price": 0.1}, (422,), "boundary:minimum:body.price@0"),
])
def test_a_step_that_only_looks_similar_does_not_cover(body, status, requirement):
    assert f"POST /items {requirement}" in covered_by(step(json_body=body, status=status))


def test_missing_the_only_required_field_counts_with_no_body_at_all():
    spec = copy.deepcopy(SPEC)
    spec["components"]["schemas"]["ItemIn"]["required"] = ["name"]
    op = cov.snapshot(spec)["operations"][1]
    facts = {"version": 1, "operations": [op]}
    assert "POST /items negative_validation:missing:body.name" not in gaps(cov.score(catalog(tc(step(status=(422,)))), facts), "technique")


def test_query_constraints_and_authz():
    assert "GET /items boundary:maximum:query.limit@51" not in covered_by(step("GET", status=(422,), query={"limit": 51}))
    assert "GET /items boundary:maximum:query.limit@51" not in covered_by(step("GET", status=(422,), query={"limit": "51"}))   # query thường là chuỗi
    assert "GET /items boundary:maximum:query.limit@51" in covered_by(step("GET", status=(422,), query={"limit": 52}))
    assert "POST /items authz:unauthenticated" not in covered_by(step(status=(401,)))
    assert "POST /items authz:unauthenticated" not in covered_by(step(status=(403,)))
    assert "POST /items authz:unauthenticated" in covered_by(step(status=(401, 201)))       # lẫn mã 2xx: không phải kỳ vọng "bị từ chối"
    assert "POST /items authz:unauthenticated" in covered_by(step(status=(422,)))


def test_the_technique_label_a_test_case_claims_is_ignored():
    liar = tc(step(json_body=VALID, status=(201,)), technique="boundary", rationale="boundary of everything")
    assert "POST /items boundary:max_length:body.name@11" in gaps(cov.score(catalog(liar), FACTS), "technique")


def test_steps_of_other_operations_do_not_count():
    assert "POST /items boundary:max_length:body.name@11" in covered_by(step("PUT", "/items", json_body={**VALID, "name": "a" * 11}, status=(422,)))
    assert "POST /items boundary:max_length:body.name@11" in covered_by(step("POST", "/other", json_body={**VALID, "name": "a" * 11}, status=(422,)))


# ---------------- API ----------------

def test_api_dimension_counts_declared_numeric_codes_and_exempts_default_and_5xx():
    report = cov.score(catalog(), FACTS)
    assert report.api.total == 3 and set(report.api.gaps) == {"POST /items 201", "POST /items 422", "GET /items 200"}


def test_a_step_covers_a_code_only_when_it_expects_exactly_that_code():
    wide = tc(step(json_body=VALID, status=(200, 201, 422)))
    assert gaps(cov.score(catalog(wide), FACTS), "api") == {"POST /items 201", "POST /items 422", "GET /items 200"}
    exact = tc(step(json_body=VALID, status=(201,)), step("GET", status=(200,)))
    assert gaps(cov.score(catalog(exact), FACTS), "api") == {"POST /items 422"}


# ---------------- AC, trạng thái, waiver ----------------

def test_ac_dimension_ignores_rejected_and_draft_in_gate_mode_and_counts_uncovered_as_waived():
    assert cov.score(catalog(tc(step(), status="rejected")), None).ac.gaps == ("AC-1",)
    assert cov.score(catalog(tc(step(), status="draft")), None).ac.gaps == ("AC-1",)
    assert cov.score(catalog(tc(step(), status="draft")), None, tc_statuses=("draft", "approved")).ac.gaps == ()
    report = cov.score(catalog(uncovered=["AC-1"]), None)
    assert (report.ac.covered, report.ac.waived, report.ac.ratio, report.technique, report.api) == (0, 1, 1.0, None, None)


def test_waivers_must_match_the_gap_id_and_be_approved_in_gate_mode():
    target = "POST /items 422"
    base = {"kind": "api", "target": target, "reason_code": "not_applicable", "reason": "x", "status": "approved"}
    report = cov.score(catalog(waivers=[base]), FACTS)
    assert report.api.waived == 1 and target not in report.api.gaps
    for bad in ({"status": "draft"}, {"status": "rejected"}, {"target": "POST /items 201 "}, {"kind": "technique"}):
        assert target in cov.score(catalog(waivers=[{**base, **bad}]), FACTS).api.gaps, bad
    draft = {**base, "status": "draft"}
    assert target not in cov.score(catalog(waivers=[draft]), FACTS, waiver_statuses=("draft", "approved")).api.gaps   # chế độ vòng agent


def test_technique_waiver_uses_the_full_gap_id():
    gap = "POST /items boundary:max_length:body.name@11"
    waiver = {"kind": "technique", "target": gap, "reason_code": "out_of_scope", "reason": "x", "status": "approved"}
    assert gap not in gaps(cov.score(catalog(waivers=[waiver]), FACTS), "technique")
    assert gap in gaps(cov.score(catalog(waivers=[{**waiver, "target": "boundary:max_length:body.name@11"}]), FACTS), "technique")


def test_empty_denominator_is_100_percent_and_thresholds_gate_the_report():
    report = cov.score(catalog(), {"version": 1, "operations": []})
    assert report.technique.ratio == report.api.ratio == 1.0
    full = cov.score(catalog(uncovered=["AC-1"]), {"version": 1, "operations": []})
    assert full.complete() and full.shortfalls({}) == {}
    partial = cov.score(catalog(), {"version": 1, "operations": []})
    assert not partial.complete() and set(partial.shortfalls({})) == {"ac"} and partial.complete({"ac": 0})


def test_as_dict_caps_the_gap_list_but_reports_the_total():
    out = cov.score(catalog(), FACTS).as_dict(limit=1)
    assert out["api"]["gaps_total"] == 3 and len(out["api"]["gaps"]) == 1 and out["ac"]["ratio"] == 0.0


# ---------------- policy và snapshot trên đĩa ----------------

def test_policy_defaults_and_validation(tmp_path):
    assert cov.load_policy(tmp_path) == ({"ac": 1.0, "technique": 1.0, "api": 1.0}, False)
    path = tmp_path / cov.POLICY_PATH
    path.parent.mkdir(parents=True)
    path.write_text("version: 1\nthresholds: {api: 0.8}\n", encoding="utf-8")
    assert cov.load_policy(tmp_path) == ({"ac": 1.0, "technique": 1.0, "api": 0.8}, True)
    for text in ("version: 2\nthresholds: {}\n", "version: 1\nthresholds: {ac: 1.5}\n", "version: 1\nthresholds: {lines: 1}\n", "version: 1\nthresholds: {ac: true}\n",
                 "version: 1\nthresholds: {ac: 1}\nextra: 1\n", "- not a mapping\n", "version: [unclosed\n"):
        path.write_text(text, encoding="utf-8")
        with pytest.raises(cov.CoverageError) as error:
            cov.load_policy(tmp_path)
        assert "unclosed" not in str(error.value)   # không trích lại nội dung file


def test_snapshot_loading(tmp_path):
    assert cov.load_snapshot(tmp_path) is None
    path = tmp_path / cov.SNAPSHOT_PATH
    path.parent.mkdir(parents=True)
    path.write_text(cov.snapshot_text(FACTS), encoding="utf-8")
    assert cov.load_snapshot(tmp_path) == FACTS
    for text in ("{not json SECRETWORD", "[]", '{"version": 9, "operations": []}', '{"version": 1, "operations": [{"method": "GET"}]}'):
        path.write_text(text, encoding="utf-8")
        with pytest.raises(cov.CoverageError) as error:
            cov.load_snapshot(tmp_path)
        assert "SECRETWORD" not in str(error.value)


# ---------------- noteboard thật: baseline và cổng validate ----------------

def validate(capsys, sut):
    code, out, err = gt(capsys, "validate", "--sut-root", sut)
    return code, out, err


def test_noteboard_single_shot_baseline_and_summary(tmp_path, capsys, fake):
    sut = make_sut(tmp_path)
    summary_path = tmp_path / "summary.json"
    code, out, err = gt(capsys, *generate_args(sut, "--summary-json", summary_path, egress=tmp_path / "e"))
    assert code == 0, err
    coverage = json.loads(summary_path.read_text(encoding="utf-8"))["coverage"]
    assert {k: (v["covered"], v["waived"], v["total"]) for k, v in coverage.items()} == {"ac": (23, 1, 25), "technique": (10, 0, 10), "api": (9, 0, 12)}
    assert coverage["ac"]["gaps"] == ["AC-3.5"] and len(coverage["api"]["gaps"]) == 3
    assert "coverage ac: 24/25" in out and "coverage api: 9/12" in out
    body = pr_body.render(json.loads(summary_path.read_text(encoding="utf-8")))
    assert "### Coverage" in body and "| API (operation × mã trạng thái) | 9 | 0 | 12 | 75% |" in body
    assert "- ⚠️ DELETE /notes/\\{note\\_id\\} 422" in body and "- ⚠️ AC\\-3.5" in body   # gap suy ra từ OpenAPI/PRD nên đi qua clean_md (thoát ký tự Markdown)


def test_generate_without_openapi_scores_only_acs_and_writes_no_snapshot(tmp_path, capsys, fake):
    sut = make_sut(tmp_path)
    code, out, err = gt(capsys, *generate_args(sut, openapi=False, egress=tmp_path / "e"))
    assert code == 0, err
    assert not (sut / cov.SNAPSHOT_PATH).exists() and "coverage technique/api: chưa chấm" in out


def finish_review(sut):
    """QA duyệt xong nhưng KHÔNG bỏ qua coverage: duyệt hết rồi gỡ policy nới lỏng mà helper của test_gt_cli đã ghi."""
    approve_everything(sut)
    (sut / GT / "coverage-policy.yaml").unlink()


def test_validate_blocks_an_approved_catalog_below_100_percent(tmp_path, capsys, fake):
    sut = generated(tmp_path, capsys)
    finish_review(sut)
    code, out, _ = validate(capsys, sut)
    assert code == 1
    assert "LỖI  .qc-agent/ground-truth/test-cases.yaml: coverage ac 24/25 (96%)" in out    # 23 AC có TC approved + AC-1.8 nằm trong uncovered_acs (tính là waived)
    assert "AC-3.5" in out and "coverage api 9/12 (75%)" in out and "DELETE /notes/{note_id} 422" in out
    assert "coverage technique" not in out


def test_validate_passes_once_the_gaps_are_waived_by_qa(tmp_path, capsys, fake):
    sut = generated(tmp_path, capsys)
    data = load_catalog(sut)
    data["uncovered_acs"].append({"ac_id": "AC-3.5", "reason": "không kiểm được bằng HTTP"})
    data["waivers"] = [{"kind": "api", "target": f"{m} /notes/{{note_id}}{s} 422", "reason_code": "not_applicable", "reason": "note_id là chuỗi tự do", "status": "approved"}
                       for m, s in (("GET", ""), ("DELETE", ""), ("POST", "/summarize"))]
    save_catalog(sut, data)
    finish_review(sut)
    code, out, _ = validate(capsys, sut)
    assert code == 0 and "coverage" not in out, out


def test_validate_does_not_accept_waivers_that_are_still_draft(tmp_path, capsys, fake):
    sut = generated(tmp_path, capsys)
    data = load_catalog(sut)
    data["uncovered_acs"].append({"ac_id": "AC-3.5", "reason": "x"})
    data["waivers"] = [{"kind": "api", "target": f"{m} /notes/{{note_id}}{s} 422", "reason_code": "not_applicable", "reason": "x", "status": "draft"}
                       for m, s in (("GET", ""), ("DELETE", ""), ("POST", "/summarize"))]
    save_catalog(sut, data)
    finish_review(sut)
    code, out, _ = validate(capsys, sut)
    assert code == 1 and "coverage api 9/12" in out and "coverage ac" not in out


def test_policy_thresholds_can_be_lowered_by_the_qa_owned_file(tmp_path, capsys, fake):
    sut = generated(tmp_path, capsys)
    approve_everything(sut)
    (sut / GT / "coverage-policy.yaml").write_text("version: 1\nthresholds: {ac: 0.96, technique: 1, api: 0.75}\n", encoding="utf-8")
    assert validate(capsys, sut)[0] == 0
    (sut / GT / "coverage-policy.yaml").write_text("version: 1\nthresholds: {ac: 0.97, technique: 1, api: 0.75}\n", encoding="utf-8")
    code, out, _ = validate(capsys, sut)
    assert code == 1 and "coverage ac" in out and "coverage api" not in out


def test_a_draft_catalog_only_gets_coverage_warnings(tmp_path, capsys, fake):
    sut = generated(tmp_path, capsys)   # còn toàn TC draft: gate đã lỗi vì draft, coverage chỉ là cảnh báo
    code, out, _ = validate(capsys, sut)
    assert code == 1 and "CẢNH BÁO  .qc-agent/ground-truth/test-cases.yaml: coverage ac 1/25 (4%)" in out   # chưa TC nào approved; chỉ AC-1.8 (uncovered_acs) được tính
    assert not any(line.startswith("LỖI") and "coverage" in line for line in out.splitlines())


def test_repos_without_snapshot_or_policy_keep_the_old_behavior(tmp_path, capsys, fake):
    sut = generated(tmp_path, capsys)
    finish_review(sut)
    (sut / cov.SNAPSHOT_PATH).unlink()
    code, out, _ = validate(capsys, sut)
    assert code == 0 and "coverage" not in out


@pytest.mark.parametrize("target,text", [(cov.SNAPSHOT_PATH, "{broken"), (cov.POLICY_PATH, "version: 9\n")])
def test_unreadable_snapshot_or_policy_is_exit_3(tmp_path, capsys, fake, target, text):
    sut = generated(tmp_path, capsys)
    approve_everything(sut)
    (sut / target).write_text(text, encoding="utf-8")
    code, out, err = validate(capsys, sut)
    assert code == 3 and "broken" not in err


def test_regen_rewrites_the_snapshot_and_keeps_it_when_openapi_is_not_given(tmp_path, capsys, fake):
    sut = generated(tmp_path, capsys)
    snapshot = sut / cov.SNAPSHOT_PATH
    snapshot.write_text("{}", encoding="utf-8")   # QA/máy làm hỏng: regen có --openapi phải sinh lại đúng bản chuẩn
    code, _, err = gt(capsys, "regen", *generate_args(sut, egress=tmp_path / "e2")[1:])
    assert code == 0, err
    assert json.loads(snapshot.read_text(encoding="utf-8"))["version"] == 1 and len(json.loads(snapshot.read_text(encoding="utf-8"))["operations"]) == 5
    before = snapshot.read_bytes()
    code, _, err = gt(capsys, "regen", *generate_args(sut, openapi=False, egress=tmp_path / "e3")[1:])
    assert code == 0, err
    assert snapshot.read_bytes() == before
