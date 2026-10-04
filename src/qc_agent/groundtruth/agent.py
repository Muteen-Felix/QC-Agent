"""Ground-Truth AGENT: đọc PRD + mã nguồn + OpenAPI đầy đủ qua NHIỀU lượt, nộp test case theo từng story, tự sửa theo phản hồi của code, và chỉ kết thúc khi
bộ chấm coverage tất định (coverage.py) hết gap hoặc hết ngân sách. Thay cho `generate.generate` (một lời gọi) khi bật `--agent`.

Ranh giới cứng (giữ nguyên từ generate.py và docs/core-rules.md):
  - LLM chỉ đề xuất NỘI DUNG test case. `tc_id`, `status: draft`, `origin: llm`, thứ tự, header catalog do CODE quyết định: catalog cuối đi qua `generate._assemble`
    y nguyên (cùng `_convert`, cùng schema), nên cùng một chuỗi response cho ra cùng từng byte. Agent không thể nộp TC `approved`.
  - Mọi TC được kiểm NGAY khi nộp (`_convert`: endpoint có trong OpenAPI, placeholder khớp, biến đã capture, schema theo `kind`). TC sai bị bỏ và lý do (do code dựng,
    không chứa nội dung file) được trả lại để agent sửa và nộp lại: đây là vòng tự sửa thay cho "sửa một lần" của bộ sinh một lời gọi.
  - Kỳ vọng lấy từ PRD/OpenAPI, KHÔNG từ mã (prompt + `report_spec_conflict`). `evidence` chỉ được trỏ tới file sandbox đã THẬT SỰ trả cho agent.
  - Kết thúc là việc của CODE: `finish_generation` chạy `coverage.score`; còn gap thì từ chối (tối đa `MAX_FINISH_REJECTIONS`) và liệt kê chính xác gap. Waiver/uncovered do agent
    đề xuất luôn là `draft`; chỉ QA mới biến thành `approved` (gate `gt validate` chỉ tính waiver approved).
  - Hết ngân sách hoặc lỗi API khi đã có ≥ 1 TC thì trả bộ DỞ kèm cảnh báo (`completed: false`): TC chỉ là bản nháp để QA duyệt nên bộ dở vẫn có giá trị hơn là mất sạch.
  - Không log nội dung (PRD, mã, prompt, response, title/rationale do LLM viết): chỉ số đếm. `core/` không import module này ở top-level (import lười từ cli).
"""
from __future__ import annotations

import copy
import json
import logging
from dataclasses import dataclass, field
from pathlib import Path

import httpx

from qc_agent import settings
from qc_agent.core import egress
from qc_agent.groundtruth import coverage as gt_coverage
from qc_agent.groundtruth import generate as gen
from qc_agent.groundtruth import repo_map as gt_repo_map
from qc_agent.groundtruth import schema as gt_schema
from qc_agent.groundtruth.merge import protected
from qc_agent.groundtruth.prd import ParsedPRD
from qc_agent.groundtruth.repo_tools import OpenApiTools, RepoSandbox, fence
from qc_agent.llm import agent_loop as al
from qc_agent.llm import client as llm
from qc_agent.logging_setup import event

log = logging.getLogger("qc_agent.groundtruth")

PROMPT_FILE = Path(__file__).with_name("prompts") / "gt_agent.md"
PURPOSE = "gt-agent"
FINISH_TOOL = "finish_generation"
BASE_CATEGORIES = ["prd_text", "api_spec"]
MAX_FINISH_REJECTIONS = 5
MAX_GAP_LINES = 40
MAX_EXISTING_LINES = 300
_METHODS = ["GET", "POST", "PUT", "PATCH", "DELETE"]

_STR = {"type": "string", "minLength": 1, "maxLength": 300}
READ_TOOL_SCHEMAS = {
    "list_dir": {"type": "object", "additionalProperties": False, "required": ["path", "depth"],
                 "properties": {"path": _STR, "depth": {"type": "integer", "minimum": 1, "maximum": 3}}},
    "read_file": {"type": "object", "additionalProperties": False, "required": ["path", "start_line", "max_lines"],
                  "properties": {"path": _STR, "start_line": {"type": "integer", "minimum": 1}, "max_lines": {"type": "integer", "minimum": 1, "maximum": 400}}},
    "grep": {"type": "object", "additionalProperties": False, "required": ["pattern", "path_glob", "fixed"],
             "properties": {"pattern": {"type": "string", "minLength": 1, "maxLength": 200},
                            "path_glob": {"anyOf": [{"type": "string", "maxLength": 300}, {"type": "null"}]}, "fixed": {"type": "boolean"}}},
    "openapi_operation": {"type": "object", "additionalProperties": False, "required": ["method", "path"],
                          "properties": {"method": {"enum": _METHODS}, "path": _STR}},
    "openapi_schema": {"type": "object", "additionalProperties": False, "required": ["name"], "properties": {"name": {"type": "string", "minLength": 1, "maxLength": 100}}},
}
DESCRIPTIONS = {
    "list_dir": "List a directory of the repository (path relative to the repository root, '.' for the root; depth 1-3). Secrets, VCS, dependency and build folders are hidden.",
    "read_file": "Read a source file with line numbers. start_line is 1-based; at most 400 lines per call. Reading is budgeted: do not re-read files you already have.",
    "grep": "Search the repository text files. pattern is a literal string unless fixed=false (then a regular expression). path_glob (e.g. 'app/**/*.py') narrows the search, or null.",
    "openapi_operation": "Full OpenAPI definition of one operation (parameters, request body, responses, with every $ref expanded). Use the method and path exactly as listed in <endpoints>.",
    "openapi_schema": "Full OpenAPI definition of one component schema by name, with nested $refs expanded.",
    "record_coverage_plan": "Record the coverage plan: one item per (acceptance criterion, technique) you intend to cover. Replaces any earlier plan.",
    "submit_test_cases": "Submit the test cases of ONE story. Each is checked immediately; the result lists the ones that were dropped and why. Fix and resubmit those.",
    "report_spec_conflict": "Report that the source code behaves differently from what the PRD/OpenAPI states for an acceptance criterion. Do not write a test case that matches the code.",
    FINISH_TOOL: "Finish. A deterministic scorer measures coverage; if gaps remain it returns them and you must close them (or justify them in uncovered_acs / waivers) and call this again.",
}


@dataclass(frozen=True)
class AgentResult(gen.GenerateResult):
    agent: dict = field(default_factory=dict)             # thống kê chạy (chỉ số/chuỗi định danh), đi vào summary.json
    coverage: gt_coverage.CoverageReport | None = None    # chấm trên bộ vừa sinh (draft + waiver draft)


def submit_schema() -> dict:
    """Input schema của `submit_test_cases`. Khoảng mã HTTP 100–599 bị nới (như `generate.tool_schema`): `_convert` kiểm từng TC, nên mã sai chỉ làm mất TC đó
    và agent nhận lý do để sửa, thay vì cả lần nộp của story bị từ chối ở tầng schema (mất hết TC tốt đi cùng)."""
    schema = copy.deepcopy(gt_schema.tool_input_schema("submit_test_cases"))
    status = schema["properties"]["test_cases"]["items"]["properties"]["steps"]["items"]["properties"]["expect"]["properties"]["status"]
    status["items"] = {"type": "integer"}
    return schema


def _fmt(report: gt_coverage.CoverageReport) -> str:
    return ", ".join(f"{name} {dim.covered + dim.waived}/{dim.total}" for name, dim in report.dims().items())


class _State:
    """Mọi thứ agent đã nộp. Tool handler chỉ thao tác trên đây; cuối cùng `catalog()` ghép bằng `generate._assemble`."""

    def __init__(self, prd: ParsedPRD, sandbox: RepoSandbox, facts: dict | None, *, model: str, version: str, source: str):
        self.prd, self.sandbox, self.facts, self.model, self.version, self.source = prd, sandbox, facts, model, version, source
        self.position = {ac.ac_id: (s, a) for s, story in enumerate(prd.stories) for a, ac in enumerate(story.acs)}
        self.stories = {story.story_id: {ac.ac_id for ac in story.acs} for story in prd.stories}
        self.endpoints = {(e["method"].upper(), e["path"]) for e in prd.endpoints} or None
        self.tc_schema = {"$ref": "#/$defs/testCase", "$defs": gt_schema.catalog_schema()["$defs"]}
        self.raw: list[dict] = []
        self.ids: set[str] = set()
        self.plan: list[dict] = []
        self.conflicts: dict[tuple[str, str], dict] = {}
        self.uncovered: dict[str, str] = {}
        self.waivers: list[dict] = []
        self.submissions = self.dropped = self.finish_rejections = 0
        self.completed = False

    # ---- catalog tạm / cuối ----

    def catalog(self) -> tuple[dict, list[str], list[str], int]:
        data = {"test_cases": copy.deepcopy(self.raw), "uncovered_acs": [{"ac_id": a, "reason": r} for a, r in self.uncovered.items()]}
        catalog, warnings, orphans, dropped, _merged = gen._assemble(self.prd, data, model=self.model, version=self.version, source=self.source)
        if self.plan:
            catalog["coverage_plan"] = [{**{k: v for k, v in item.items() if v is not None}, "tc_ids": [tc["tc_id"] for tc in catalog["test_cases"]
                                         if item["ac_id"] in tc["ac_refs"] and tc.get("technique") == item["technique"]]} for item in self.plan]
        if self.waivers:
            catalog["waivers"] = copy.deepcopy(self.waivers)
        if self.conflicts:
            catalog["spec_conflicts"] = sorted(copy.deepcopy(list(self.conflicts.values())), key=lambda c: (self.position[c["ac_id"]], c["summary"]))
        return catalog, warnings, orphans, dropped

    def report(self, catalog: dict | None = None) -> gt_coverage.CoverageReport:
        catalog = catalog or self.catalog()[0]
        return gt_coverage.score(catalog, self.facts, tc_statuses=("draft", "approved"), waiver_statuses=("draft", "approved"))

    def _evidence(self, items, notes: list[str], where: str) -> list[dict]:
        kept = []
        for item in items or []:
            if item["path"] in self.sandbox.served:
                kept.append(item)
            else:
                notes.append(f"{where}: evidence {item['path']} bị bỏ vì bạn chưa đọc file này (read_file/grep)")
        return kept

    # ---- tool handlers ----

    def plan_tool(self, data: dict) -> al.ToolOutcome:
        unknown = sorted({i["ac_id"] for i in data["items"] if i["ac_id"] not in self.position})
        if unknown:
            return al.ToolOutcome("ac_id không có trong PRD: " + ", ".join(unknown), is_error=True)
        self.plan = [copy.deepcopy(i) for i in data["items"]]
        planned = {i["ac_id"] for i in self.plan}
        missing = [a for a in self.position if a not in planned]
        return al.ToolOutcome(f"Plan recorded: {len(self.plan)} item(s) over {len(planned)} AC(s)." + (f" ACs not in the plan yet: {', '.join(missing[:30])}." if missing else ""))

    def submit_tool(self, data: dict) -> al.ToolOutcome:
        story = data["story_id"]
        if story not in self.stories:
            return al.ToolOutcome("story_id không có trong PRD: " + ", ".join(sorted(self.stories)[:30]), is_error=True)
        self.submissions += 1
        accepted, notes = [], []
        for number, raw in enumerate(data["test_cases"], 1):
            raw = copy.deepcopy(raw)
            raw["evidence"] = self._evidence(raw.get("evidence"), notes, f"TC #{number}")
            try:
                tc = gen._convert(raw, acs=set(self.position), endpoints=self.endpoints, tc_schema=self.tc_schema)
            except gen._Drop as drop:
                self.dropped += 1
                notes.append(f"TC #{number} bị bỏ: {drop}")
                continue
            if tc["ac_refs"][0] not in self.stories[story]:
                notes.append(f"TC #{number}: AC đầu tiên {tc['ac_refs'][0]} không thuộc story {story}; TC được xếp theo story của AC đầu tiên")
            if tc["tc_id"] in self.ids:
                notes.append(f"TC #{number} trùng nội dung với {tc['tc_id']}: gộp làm một")
            self.ids.add(tc["tc_id"])
            self.raw.append(raw)
            accepted.append(tc["tc_id"])
        dropped = sum(1 for n in notes if "bị bỏ:" in n)
        text = f"Accepted {len(accepted)} test case(s)" + (f": {', '.join(accepted[:20])}" + (" …" if len(accepted) > 20 else "") if accepted else "") + "."
        if notes:
            text += "\n" + "\n".join(f"- {n}" for n in notes[:40])
        text += f"\nCoverage now (draft included): {_fmt(self.report())}."
        return al.ToolOutcome(text, is_error=dropped > 0)

    def conflict_tool(self, data: dict) -> al.ToolOutcome:
        if data["ac_id"] not in self.position:
            return al.ToolOutcome("ac_id không có trong PRD", is_error=True)
        notes: list[str] = []
        entry = {"ac_id": data["ac_id"], "summary": " ".join(data["summary"].split())[:300], "status": "open"}
        evidence = [{"path": e["path"], **({"line": e["line"]} if e.get("line") else {})} for e in self._evidence(data["evidence"], notes, "conflict")
                    if gen._EVIDENCE_PATH.fullmatch(e["path"])]
        if evidence:
            entry["evidence"] = evidence
        self.conflicts.setdefault((entry["ac_id"], entry["summary"]), entry)
        return al.ToolOutcome("Conflict recorded for QA. Do not write a test case that matches the code." + ("".join("\n- " + n for n in notes)))

    def finish_tool(self, data: dict) -> al.ToolOutcome:
        notes: list[str] = []
        self.uncovered = {}
        for item in data["uncovered_acs"]:
            reason = gen._reason(item["reason"])
            if item["ac_id"] not in self.position:
                notes.append(f"uncovered_acs bị bỏ: {item['ac_id']} không có trong PRD")
            elif reason:
                self.uncovered.setdefault(item["ac_id"], reason)
        self.waivers = []
        base_catalog, *_ = self.catalog()
        open_gaps = set()
        base = self.report(base_catalog)
        for dim in (base.technique, base.api):
            open_gaps |= set(dim.gaps) if dim is not None else set()
        seen = set()
        for item in data["waivers"]:
            key = (item["kind"], item["target"])
            if key in seen:
                continue
            seen.add(key)
            if item["target"] not in open_gaps:
                notes.append(f"waiver bị bỏ: target {item['target']!r} không khớp gap nào đang mở (chép đúng từng ký tự sau thẻ [kind])")
                continue
            self.waivers.append({"kind": item["kind"], "target": item["target"], "reason_code": item["reason_code"],
                                 "reason": gen._reason(item["reason"]) or "-", "status": "draft"})
        report = self.report()
        gaps = [f"[{name}] {gap}" for name, dim in report.dims().items() for gap in dim.gaps]
        if not gaps or self.finish_rejections >= MAX_FINISH_REJECTIONS:
            self.completed = not gaps
            return al.ToolOutcome("Accepted." + ("" if not gaps else f" Stopped with {len(gaps)} open gap(s) (rejection limit reached); QA will see them.") + ("".join("\n- " + n for n in notes)), stop=True)
        self.finish_rejections += 1
        shown = gaps[:MAX_GAP_LINES]
        text = (f"Coverage is incomplete ({_fmt(report)}). Close every gap below, then call {FINISH_TOOL} again "
                f"({MAX_FINISH_REJECTIONS - self.finish_rejections + 1} attempt(s) left):\n" + "\n".join(shown) + (f"\n… (+{len(gaps) - len(shown)} more)" if len(gaps) > len(shown) else "")
                + "\nHow to close a gap: submit a test case that really does it (the scorer reads request bodies and expected status codes, not labels). If it truly cannot be exercised over HTTP, "
                  "list the AC in uncovered_acs, or a technique/api gap in waivers with target = the exact text after the [kind] tag, with a real reason."
                + ("".join("\n- " + n for n in notes)))
        return al.ToolOutcome(text, is_error=True)


def _tools(state: _State, sandbox: RepoSandbox, api: OpenApiTools | None) -> list[al.AgentTool]:
    s = READ_TOOL_SCHEMAS
    tools = [
        al.AgentTool("list_dir", DESCRIPTIONS["list_dir"], s["list_dir"], lambda d: sandbox.list_dir(d["path"], d["depth"])),
        al.AgentTool("read_file", DESCRIPTIONS["read_file"], s["read_file"], lambda d: sandbox.read_file(d["path"], d["start_line"], d["max_lines"])),
        al.AgentTool("grep", DESCRIPTIONS["grep"], s["grep"], lambda d: sandbox.grep(d["pattern"], d["path_glob"], d["fixed"])),
    ]
    if api is not None:
        tools += [al.AgentTool("openapi_operation", DESCRIPTIONS["openapi_operation"], s["openapi_operation"], lambda d: api.operation(d["method"], d["path"])),
                  al.AgentTool("openapi_schema", DESCRIPTIONS["openapi_schema"], s["openapi_schema"], lambda d: api.schema(d["name"]))]
    handlers = {"record_coverage_plan": state.plan_tool, "submit_test_cases": state.submit_tool, "report_spec_conflict": state.conflict_tool, FINISH_TOOL: state.finish_tool}
    # `submit_test_cases` KHÔNG strict: schema ~6 KB bị API từ chối (400 "compiled grammar is too large", đo bằng API thật 2026-10-02) dù một mình hay cùng tool khác.
    # Không mất chốt chặn: `agent_loop._run_tool` vẫn validate bằng schema đầy đủ và trả lý do cho model sửa; `_convert` kiểm tiếp từng TC.
    tools += [al.AgentTool(name, DESCRIPTIONS[name], submit_schema() if name == "submit_test_cases" else gt_schema.tool_input_schema(name), handler,
                           strict=name != "submit_test_cases")
              for name, handler in handlers.items()]
    return tools   # tool cuối cùng nhận cache_control: giữ finish_generation ở cuối cho thứ tự ổn định


def _existing_block(existing: dict | None) -> str | None:
    if not existing:
        return None
    lines = []
    for tc in existing.get("test_cases", []):
        if not protected(tc):
            continue
        steps = ["{} {}".format(s["request"]["method"], s["request"]["path"]) + "->" + ",".join(str(c) for c in s["expect"]["status"]) for s in tc["steps"]]
        lines.append(json.dumps({"tc_id": tc["tc_id"], "ac_refs": tc["ac_refs"], "status": tc["status"], "steps": steps}, ensure_ascii=False, sort_keys=True))
    if not lines:
        return None
    more = f"\n… (+{len(lines) - MAX_EXISTING_LINES} more)" if len(lines) > MAX_EXISTING_LINES else ""
    return "<existing_cases>\nThese cases are already decided by QA. Do not submit duplicates of them.\n" + fence("\n".join(lines[:MAX_EXISTING_LINES]) + more) + "\n</existing_cases>"


def build_first_user(prd: ParsedPRD, overview: str, existing: dict | None = None, repo_map_text: str = "") -> str:
    parts = [gen.build_user(prd)]
    if overview.strip():
        parts.append("<repo_overview>\n" + fence(overview) + "\n</repo_overview>")   # RepoSandbox.overview đã fence; fence lần hai vô hại và không phụ thuộc người gọi
    if repo_map_text.strip():
        parts.append("<repo_map>\n" + fence(repo_map_text) + "\n</repo_map>")
    block = _existing_block(existing)
    if block:
        parts.append(block)
    parts.append("Begin. Explore only what you need, record the coverage plan, submit the test cases story by story, and finish with finish_generation.")
    return "\n\n".join(parts)


def generate_agent(prd: ParsedPRD, *, model: str, egress_dir: Path, source_root: Path, openapi_spec: dict | None = None, facts: dict | None = None,
                   existing: dict | None = None, transport: httpx.BaseTransport | None = None, policy: egress.EgressPolicy | None = None,
                   source: str | None = None, budget: al.AgentBudget | None = None, repo_map: dict | None = None, use_repo_map: bool = True) -> AgentResult:
    """Chạy agent -> catalog `draft` (cùng hình dạng và cùng ràng buộc với `generate.generate`). Lỗi luôn là `GTError`.

    `source_root`: thư mục mã nguồn được đọc (CI: bản `origin/<base>` mount `:ro`, không phải nhánh bot có thể đã cũ). `openapi_spec`: OpenAPI ĐẦY ĐỦ (cho tool
    `openapi_*`); `facts` mặc định suy ra từ nó (`coverage.snapshot`). `existing`: catalog cũ khi regen (TC đã quyết được liệt kê để agent không nộp trùng).
    `repo_map`: kết quả `repo_map.build` có sẵn (không thì tự quét; `use_repo_map=False` để tắt): bản tóm tắt tất định do code quét, là tầng 1 của việc đọc mã;
    lỗi quét chỉ làm mất bản tóm tắt chứ không làm hỏng lần chạy.
    Hết ngân sách / lỗi API khi đã có ≥ 1 TC thì trả bộ dở (`agent["completed"] = False`, kèm cảnh báo); chưa có TC nào thì ném `GTError`.
    """
    total = sum(len(story.acs) for story in prd.stories)
    if total == 0:
        raise gen.GTError("no_acs", "PRD không có acceptance criteria nào để sinh test case")   # chưa gửi gì đi, không ghi egress
    cfg = settings.get()
    if llm.provider_of(model) != "anthropic":
        raise gen.GTError("bad_request", "agent chỉ hỗ trợ model Claude (đặt QC_GT_AGENT_MODEL=claude-*)")
    try:
        sandbox = RepoSandbox(source_root, max_total_bytes=cfg.gt_agent_max_read_bytes)
    except (OSError, ValueError):
        raise gen.GTError("bad_request", "source_root không phải thư mục đọc được") from None
    version, system = gen.load_prompt(PROMPT_FILE)
    if facts is None and openapi_spec is not None:
        facts = gt_coverage.snapshot(openapi_spec)
    state = _State(prd, sandbox, facts, model=model, version=version, source=source or prd.prd_id)
    api = OpenApiTools(openapi_spec) if openapi_spec is not None else None
    overview = sandbox.overview(depth=2)
    map_text, map_stats, map_error = "", None, None
    if use_repo_map:
        try:
            built = repo_map if repo_map is not None else gt_repo_map.build(source_root, sandbox=sandbox)
            map_text, map_stats = gt_repo_map.render_text(built), built["stats"]
        except Exception as error:  # noqa: BLE001 — chỉ là gợi ý: ghi loại lỗi, không trích nội dung
            map_error = type(error).__name__
    categories = [*BASE_CATEGORIES, *(["source_code"] if overview.strip() or map_text.strip() else [])]
    budget = budget or al.AgentBudget(max_turns=cfg.gt_agent_max_turns, max_cost_usd=cfg.gt_agent_max_cost_usd, max_wall_s=cfg.gt_agent_max_wall_s)

    run: al.AgentRun | None = None
    failure: llm.LLMError | None = None
    try:
        run = al.run_agent(purpose=PURPOSE, model=model, system=system, first_user=build_first_user(prd, overview, existing, map_text), tools=_tools(state, sandbox, api),
                           finish_tool=FINISH_TOOL, egress_dir=egress_dir, base_categories=categories, budget=budget, effort=cfg.gt_agent_effort,
                           timeout_s=cfg.gt_agent_timeout_s, fallbacks=cfg.gt_agent_fallbacks, policy=policy, transport=transport)
    except llm.LLMError as error:
        if not state.raw:
            raise gen.GTError(error.kind, str(error)) from None
        failure = error
        run = getattr(error, "partial", None)   # lượt/token/chi phí đã tốn trước khi lỗi: không có thì thống kê báo 0 dù tiền đã bị tính

    catalog, warnings, orphans, dropped = state.catalog()
    problems = gt_schema.validate_catalog(catalog)
    if problems:   # lỗi lập trình (ghép catalog sai schema), không phải lỗi của LLM
        raise gen.GTError("bad_output", "catalog sinh ra vi phạm schema: " + "; ".join(problems[:3]))
    if not catalog["test_cases"]:
        raise gen.GTError("bad_output", "agent không nộp được test case hợp lệ nào")
    if map_error:
        warnings.append(f"không quét được repo map ({map_error}): agent tự khám phá bằng list_dir/grep/read_file")
    if state.dropped:
        warnings.append(f"{state.dropped} test case bị bỏ khi nộp (vi phạm ngữ nghĩa; agent nhận lý do ngay và có thể đã nộp lại bản sửa)")
    if failure is not None:
        warnings.append(f"agent dừng vì lỗi ({failure.kind}) sau khi đã nộp {len(catalog['test_cases'])} test case: bộ này CHƯA đủ, kiểm các gap trong coverage")
    elif run is not None and run.stop != "finished":
        warnings.append(f"agent dừng do hết ngân sách ({run.stop}): bộ này CHƯA đủ, kiểm các gap trong coverage")
    elif not state.completed:
        warnings.append("agent kết thúc khi coverage chưa đủ (đã hết lượt nhắc): kiểm các gap trong coverage")
    report = state.report(catalog)
    usage = run.usage if run is not None else llm.Usage()
    techniques: dict[str, int] = {}
    for tc in catalog["test_cases"]:
        techniques[tc.get("technique", "unlabeled")] = techniques.get(tc.get("technique", "unlabeled"), 0) + 1
    stats = {"turns": run.turns if run else 0, "stop": run.stop if run else "error", "completed": bool(state.completed and failure is None),
             "error_kind": failure.kind if failure else None, "nudges": run.nudges if run else 0, "tool_calls": dict(run.tool_calls) if run else {},
             "files_read": len(sandbox.served), "bytes_read": sandbox.bytes_served, "denied_reads": sandbox.denied_hits, "submissions": state.submissions,
             "dropped_in_loop": state.dropped, "finish_rejections": state.finish_rejections, "waivers": len(state.waivers), "spec_conflicts": len(state.conflicts),
             "cost_usd_est": None if (run is None or run.cost_usd_est is None) else round(run.cost_usd_est, 4), "model": run.model if run else model,
             "duration_s": round(run.duration_s, 3) if run else 0.0,
             "techniques": dict(sorted(techniques.items())),
             "repo_map": {**(map_stats or {}), "chars": len(map_text)} if map_text else None}
    event(log, "gt.agent", logging.INFO, stories=len(prd.stories), acs=total, test_cases=len(catalog["test_cases"]), orphans=len(orphans), dropped=dropped,
          **{k: v for k, v in stats.items() if k in ("turns", "stop", "completed", "files_read", "bytes_read", "submissions", "dropped_in_loop", "finish_rejections", "waivers", "spec_conflicts")})
    return AgentResult(catalog=catalog, usage=usage, warnings=tuple(warnings), orphans=tuple(orphans), dropped=state.dropped + dropped, agent=stats, coverage=report)
