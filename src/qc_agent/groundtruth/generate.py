"""Sinh test case Ground-Truth bằng LLM (S1-04): `ParsedPRD` -> MỘT lời gọi tool-use -> catalog `draft`, TẤT ĐỊNH với cùng một response.

Ranh giới cứng:
  - LLM chỉ đề xuất NỘI DUNG test case (tiêu đề, request, assertion). Mọi thứ còn lại do code quyết định: `tc_id`, thứ tự, `status`, `origin`, header catalog.
    Catalog không chứa token, thời gian hay usage (render hai lần phải ra byte giống hệt); `usage` trả riêng cho caller.
  - PRD và danh sách endpoint là dữ liệu KHÔNG TIN CẬY: nằm trong `<prd>` / `<endpoints>`, thẻ đóng bị vô hiệu hoá nên nội dung không thoát ra ngoài vùng phân cách.
    Đầu ra bị ép vào schema tool (enum, regex, closed assertion set) rồi kiểm ngữ nghĩa từng TC; TC vi phạm bị BỎ (kèm warning), không làm hỏng cả lần sinh.
  - Không log nội dung (PRD, prompt, response, title/reason do LLM viết): chỉ số đếm. Warning/lỗi chỉ chứa số thứ tự TC và ID/path đã qua regex của schema.
  - `core/` không import module này ở top-level (import lười từ `core/cli.py`, S1-06).
"""
from __future__ import annotations

import copy
import hashlib
import json
import logging
import re
from dataclasses import asdict, dataclass
from pathlib import Path

import httpx
import yaml

from qc_agent import costing, settings
from qc_agent.core import egress
from qc_agent.groundtruth import cache as gt_cache
from qc_agent.groundtruth import schema as gt_schema
from qc_agent.groundtruth.prd import ParsedPRD
from qc_agent.llm import client as llm
from qc_agent.llm import prices
from qc_agent.logging_setup import event

log = logging.getLogger("qc_agent.groundtruth")

PROMPT_FILE = Path(__file__).with_name("prompts") / "gt_generate.md"
TOOL_NAME = "emit_test_cases"
TOOL_DESCRIPTION = "Emit the black-box HTTP test cases for the PRD, and list the acceptance criteria that cannot be checked over HTTP."
MAX_TOKENS = 16000          # request non-streaming ~16k: đủ cho PRD cỡ vừa mà không phải chuyển sang streaming
MIN_TIMEOUT_S = 300.0       # non-streaming: API im lặng tới khi xong cả 16k token, nên read-timeout mặc định 120s của client là quá ngắn
REASON_MAX = 300
DATA_CATEGORIES = ["prd_text", "api_spec"]
_VERSION = re.compile(r"[a-z0-9-]+/[0-9]+")
_VAR_NAME = re.compile(r"[a-z][a-z0-9_]{0,39}")
_VAR_USE = re.compile(r"\{\{(.*?)\}\}")
_PLACEHOLDER = re.compile(r"\{([^{}/]*)\}")
_TAG = re.compile(r"<(?=/?\s*(?:prd|endpoints|validation_error|repo_overview|repo_map|existing_cases|file|listing|matches|openapi|tool_output)\b)", re.I)
_STATUS_MIN, _STATUS_MAX = 100, 599
_EVIDENCE_PATH = re.compile(r"[.]?[A-Za-z0-9_-][A-Za-z0-9_.-]*(/[.]?[A-Za-z0-9_-][A-Za-z0-9_.-]*)*")   # phải khớp $defs/evidence.path (không có đoạn `.` hay `..`)


class GTError(RuntimeError):
    """Không sinh được catalog: thiếu khoá, egress bị chặn, LLM lỗi hoặc trả sai schema kể cả sau một lần sửa, hoặc PRD không có AC nào.
    `.kind` là một trong `llm.KINDS`, `no_acs` hoặc `token_cap` (ước lượng đầu vào vượt `QC_LLM_MAX_INPUT_TOKENS`: chưa gửi gì). Thông điệp không chứa nội dung PRD/prompt/response.
    CLI (S1-06) trả exit 3. `calls` là các lời gọi ĐÃ GỬI trước khi lỗi (kể cả lần bị từ chối schema, lần timeout chưa rõ chi phí) để CLI vẫn ghi `llm_usage.json`."""

    def __init__(self, kind: str, detail: str = "", *, calls: tuple = ()):
        super().__init__(f"{kind}: {detail}" if detail else kind)
        self.kind = kind
        self.calls = tuple(calls)


@dataclass(frozen=True)
class CallRecord:
    """Một lời gọi LLM đã GỬI (S4-03), đủ để ghi một dòng `llm_usage.json`. `usage=None` nghĩa là CHƯA BIẾT (đã gửi, không có response): không phải 0.
    `status` là `ok` hoặc `LLMError.kind`. `cache_hit=True`: không có request lần này, `usage` là của lần tạo (không được cộng vào chi phí). `turns` chỉ có ở dòng tổng của agent."""
    purpose: str
    model: str
    prompt_version: str | None
    status: str
    usage: llm.Usage | None
    duration_s: float | None = None
    unknown_calls: int = 0
    cache_hit: bool = False
    turns: int | None = None


def usage_rows(calls) -> list[dict]:
    """Các dòng `llm_usage.json` (xem `qc_agent/costing.py` cho ngữ nghĩa `null`). `est_usd` tính bằng bảng giá DUY NHẤT `llm/prices.py`."""
    return [costing.row(purpose=c.purpose, model=c.model, prompt_version=c.prompt_version, usage=None if c.usage is None else asdict(c.usage),
                        est_usd=None if c.usage is None else prices.estimate_cost(c.model, c.usage), cache_hit=c.cache_hit, duration_s=c.duration_s,
                        status=c.status, unknown_calls=c.unknown_calls, turns=c.turns) for c in calls]


def total_usage(calls) -> llm.Usage:
    """Tổng token của MỌI lời gọi đã biết usage (kể cả lần bị từ chối); lời gọi chưa biết usage không góp số nào (nó nằm ở `unknown_calls`)."""
    total = llm.Usage()
    for c in calls:
        if c.usage is not None:
            total = llm.Usage(total.input_tokens + c.usage.input_tokens, total.output_tokens + c.usage.output_tokens,
                              total.cache_creation_input_tokens + c.usage.cache_creation_input_tokens, total.cache_read_input_tokens + c.usage.cache_read_input_tokens)
    return total


@dataclass(frozen=True)
class GenerateResult:
    catalog: dict                    # đã qua validate_catalog; mọi TC `draft`/`llm`
    usage: llm.Usage                 # TỔNG các lời gọi đã biết usage, kể cả lần bị từ chối schema (S4-03); khi `cache_hit` là của lần sinh gốc
    warnings: tuple[str, ...]
    orphans: tuple[str, ...]         # AC không có TC và cũng không nằm trong uncovered_acs, theo thứ tự PRD
    dropped: int = 0                 # số TC bị bỏ vì vi phạm ngữ nghĩa
    cache_hit: bool = False          # True: không gọi LLM (cache.py); `usage` là của lần sinh gốc, người cộng chi phí phải bỏ qua lần này
    calls: tuple = ()                # CallRecord của từng lời gọi đã gửi (kể cả lần bị từ chối); nguồn của llm_usage.json


class _Drop(Exception):
    """TC này vi phạm ngữ nghĩa: bỏ, kèm lý do (không chứa nội dung do LLM viết)."""


def load_prompt(file: Path | None = None) -> tuple[str, str]:
    """(prompt_version, system) từ prompts/gt_generate.md (hoặc `file`, vd prompts/gt_agent.md). Nội dung prompt đổi thì phải tăng version (S4-02 dùng làm khoá cache)."""
    file = file or PROMPT_FILE
    text = file.read_text(encoding="utf-8").replace("\r\n", "\n")
    if not text.startswith("---\n") or text.find("\n---\n", 3) == -1:
        raise ValueError(f"{file.name} thiếu front-matter")
    end = text.find("\n---\n", 3)
    front = yaml.safe_load(text[4:end])
    version = front.get("prompt_version") if isinstance(front, dict) else None
    if not isinstance(version, str) or not _VERSION.fullmatch(version):
        raise ValueError(f"{file.name}: prompt_version phải dạng <tên>/<số>")
    return version, text[end + 5:].strip()


def tool_schema() -> dict:
    """Input schema của tool. Khoảng mã HTTP 100–599 bị nới ở đây để `_convert` kiểm từng TC: mã sai chỉ làm mất TC đó, không tốn cả lần gọi."""
    schema = copy.deepcopy(gt_schema.emit_schema())
    status = schema["properties"]["test_cases"]["items"]["properties"]["steps"]["items"]["properties"]["expect"]["properties"]["status"]
    status["items"] = {"type": "integer"}
    return schema


# ---------------- prompt ----------------

def _fence(text: str) -> str:
    """Vô hiệu hoá thẻ mở/đóng của vùng phân cách bên trong dữ liệu: `</prd>` thành `&lt;/prd>`."""
    return _TAG.sub("&lt;", text)


def build_user(prd: ParsedPRD, *, repair: str | None = None, auth: str | None = None) -> str:
    index = "\n".join(f"{story.story_id}: {story.title}\n" + "\n".join(f"  [{ac.ac_id}] {ac.text}" for ac in story.acs) for story in prd.stories)
    parts = ["Generate the test cases for the PRD below.\n",
             "<prd>\n" + _fence(prd.text.strip()) + "\n\n=== AC index (extracted by code; use these ids in ac_refs and uncovered_acs) ===\n" + _fence(index) + "\n</prd>"]
    if prd.endpoints:
        rows = [{"method": e["method"], "path": e["path"], "parameters": e["parameters"], "body_required": e["body_required"], "responses": e["responses"]}
                for e in prd.endpoints]
        parts.append("<endpoints>\n" + _fence("\n".join(json.dumps(row, ensure_ascii=False, sort_keys=True) for row in rows)) + "\n</endpoints>")
    if auth:
        parts.append(auth)   # khối <auth> do code dựng (groundtruth/auth.py), không chứa token hay khoá
    if repair:
        parts.append("<validation_error>\nYour previous call was rejected by schema validation: " + repair
                     + "\nCall emit_test_cases again with the same intent and a corrected, schema-valid input.\n</validation_error>")
    return "\n\n".join(parts)


# ---------------- emit -> catalog ----------------

def _canonical(value) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _no_constant(name: str):
    raise ValueError(name)


def _name_map(items: list[dict], what: str) -> dict:
    out: dict = {}
    for item in items:
        if item["name"] in out:
            raise _Drop(f"{what} trùng tên {item['name']}")
        out[item["name"]] = item["value"]
    return out


def _uses(text: str, defined: set[str], where: str) -> None:
    """Mọi `{{x}}` trong `text` phải là biến đã capture ở bước trước; `{{`/`}}` lẻ là sai cú pháp."""
    for name in _VAR_USE.findall(text):
        if not _VAR_NAME.fullmatch(name):
            raise _Drop(f"biến sai cú pháp trong {where}")
        if name not in defined:
            raise _Drop(f"biến {{{{{name}}}}} chưa được capture ở bước trước ({where})")
    if "{{" in _VAR_USE.sub("", text) or "}}" in _VAR_USE.sub("", text):
        raise _Drop(f"dấu {{{{ hoặc }}}} lẻ trong {where}")


def _walk_json(node, defined: set[str]) -> None:
    if isinstance(node, str):
        _uses(node, defined, "json")
    elif isinstance(node, list):
        for item in node:
            _walk_json(item, defined)
    elif isinstance(node, dict):
        for key, value in node.items():
            if "{{" in key or "}}" in key:
                raise _Drop("biến nằm trong khoá của json")
            _walk_json(value, defined)


def _convert_step(raw: dict, defined: set[str], endpoints: set[tuple[str, str]] | None) -> dict:
    request = raw["request"]
    method, path = request["method"], request["path"]
    if endpoints is not None and (method, path) not in endpoints:
        raise _Drop(f"endpoint {method} {path} không có trong danh sách")
    if "{{" in path or "}}" in path:
        raise _Drop("biến {{…}} không được dùng trong path (chỉ trong path_params, query, json)")
    path_params = _name_map(request["path_params"], "path_params")
    placeholders = set(_PLACEHOLDER.findall(path))
    if placeholders != set(path_params):
        raise _Drop(f"path_params không khớp placeholder của {method} {path}")
    query = _name_map(request["query"], "query")
    headers = _name_map(request["headers"], "headers")
    for name, value in path_params.items():
        if isinstance(value, str):
            _uses(value, defined, "path_params")
    for value in query.values():
        if isinstance(value, str):
            _uses(value, defined, "query")
    for value in headers.values():
        if "{{" in value or "}}" in value:
            raise _Drop("biến {{…}} không được dùng trong headers")
    body = None
    if request["json"] is not None:
        try:
            body = json.loads(request["json"], parse_constant=_no_constant)
        except (ValueError, RecursionError):
            raise _Drop("json không phải chuỗi JSON hợp lệ") from None
        _walk_json(body, defined)
    codes = raw["expect"]["status"]
    if any(isinstance(code, bool) or not _STATUS_MIN <= code <= _STATUS_MAX for code in codes):
        raise _Drop(f"status ngoài khoảng {_STATUS_MIN}–{_STATUS_MAX}")

    out_request: dict = {"method": method, "path": path}
    if path_params:
        out_request["path_params"] = path_params
    if query:
        out_request["query"] = query
    if headers:
        out_request["headers"] = headers
    if body is not None:
        out_request["json"] = body
    expect: dict = {"status": list(codes)}
    if raw["expect"]["json"]:
        expect["json"] = [dict(item) for item in raw["expect"]["json"]]
    step: dict = {"request": out_request, "expect": expect}
    captures = _name_map([{"name": c["name"], "value": c["path"]} for c in raw["capture"]], "capture")
    if captures:
        step["capture"] = captures
    return step


def _convert(raw: dict, *, acs: set[str], endpoints: set[tuple[str, str]] | None, tc_schema: dict) -> dict:
    title = " ".join(raw["title"].split())
    if not title:
        raise _Drop("title rỗng")
    refs = list(dict.fromkeys(raw["ac_refs"]))
    unknown = [ref for ref in refs if ref not in acs]
    if unknown:
        raise _Drop("ac_refs không có trong PRD: " + ", ".join(unknown))
    defined: set[str] = set()
    steps = []
    for raw_step in raw["steps"]:
        step = _convert_step(raw_step, defined, endpoints)
        steps.append(step)
        defined.update(step.get("capture", {}))
    digest = hashlib.sha1(_canonical({"kind": raw["kind"], "steps": steps}).encode("utf-8")).hexdigest()[:6]
    tc = {"tc_id": f"TC-{refs[0]}-{digest}", "title": title, "ac_refs": refs, "kind": raw["kind"], "status": "draft", "origin": "llm", "steps": steps}
    tc.update(_metadata(raw))
    problems = gt_schema.errors(tc, tc_schema)   # luật theo kind: flow >= 2 bước, api_functional <= 1 bước, api_contract chỉ method+path
    if problems:
        raise _Drop("vi phạm schema catalog tại " + "; ".join(problems[:3]))
    return tc


def _reason(text: str) -> str:
    return " ".join(text.split())[:REASON_MAX].strip()


def _metadata(raw: dict) -> dict:
    """Trường tuỳ chọn mà chỉ bộ sinh dạng agent điền (priority, technique, preconditions, rationale, evidence). Single-shot không có khoá nào nên cho ra {}.
    Không đổi `tc_id` (băm theo kind+steps) nên khử trùng và `merge` vẫn như cũ. Chuỗi được co khoảng trắng và cắt độ dài; giá trị rỗng/null bị bỏ."""
    out: dict = {}
    for key in ("priority", "technique"):
        if raw.get(key):
            out[key] = raw[key]
    for key in ("preconditions", "rationale"):
        text = " ".join(raw[key].split())[:REASON_MAX].strip() if isinstance(raw.get(key), str) else ""
        if text:
            out[key] = text
    # fullmatch chứ không chỉ dựa regex của schema: `$` của schema chấp nhận xuống dòng ở cuối, và đường dẫn này đi vào YAML/Excel mà QA mở.
    evidence = [{"path": e["path"], **({"line": e["line"]} if e.get("line") else {})} for e in raw.get("evidence") or [] if _EVIDENCE_PATH.fullmatch(e["path"])]
    if evidence:
        out["evidence"] = evidence[:5]
    return out


def _assemble(prd: ParsedPRD, data: dict, *, model: str, version: str, source: str) -> tuple[dict, list[str], list[str], int, int]:
    """emit -> (catalog, warnings, orphans, dropped, merged). Hàm tất định: chỉ phụ thuộc vào `data` và `prd`."""
    position = {ac.ac_id: (s, a) for s, story in enumerate(prd.stories) for a, ac in enumerate(story.acs)}
    endpoints = {(e["method"].upper(), e["path"]) for e in prd.endpoints} or None
    catalog_schema = gt_schema.catalog_schema()
    tc_schema = {"$ref": "#/$defs/testCase", "$defs": catalog_schema["$defs"]}
    warnings: list[str] = []
    if endpoints is None:
        warnings.append("PRD không kèm OpenAPI: không kiểm được endpoint của TC")

    kept: dict[str, dict] = {}
    dropped = merged = 0
    for number, raw in enumerate(data["test_cases"], 1):
        try:
            tc = _convert(raw, acs=set(position), endpoints=endpoints, tc_schema=tc_schema)
        except _Drop as drop:
            dropped += 1
            warnings.append(f"TC #{number} bị bỏ: {drop}")
            continue
        prior = kept.get(tc["tc_id"])
        if prior is None:
            kept[tc["tc_id"]] = tc
            continue
        merged += 1
        prior["ac_refs"] = list(dict.fromkeys([*prior["ac_refs"], *tc["ac_refs"]]))
        warnings.append(f"TC #{number} trùng nội dung với {tc['tc_id']}: gộp làm một")
    test_cases = sorted(kept.values(), key=lambda tc: (position[tc["ac_refs"][0]], tc["tc_id"]))
    covered = {ref for tc in test_cases for ref in tc["ac_refs"]}

    uncovered: dict[str, str] = {}
    for item in data["uncovered_acs"]:
        ac_id, reason = item["ac_id"], _reason(item["reason"])
        if ac_id not in position:
            warnings.append(f"uncovered_acs bị bỏ: {ac_id} không có trong PRD")
        elif ac_id in covered:
            warnings.append(f"uncovered_acs bị bỏ: {ac_id} đã có test case")
        elif not reason:
            warnings.append(f"uncovered_acs bị bỏ: {ac_id} không nêu lý do")
        else:
            uncovered.setdefault(ac_id, reason)
    orphans = [ac_id for ac_id in position if ac_id not in covered and ac_id not in uncovered]

    catalog = {
        "version": 1,
        "prd": {"id": prd.prd_id, "sha256": prd.sha256, "source": source},
        "generated_by": {"model": model, "prompt_version": version},
        "status": "draft",
        "stories": [{"story_id": story.story_id, "title": story.title, "acs": [{"ac_id": ac.ac_id, "text": ac.text} for ac in story.acs]}
                    for story in prd.stories],
        "test_cases": test_cases,
        "uncovered_acs": [{"ac_id": ac_id, "reason": uncovered[ac_id]} for ac_id in sorted(uncovered, key=position.__getitem__)],
    }
    return catalog, warnings, orphans, dropped, merged


# ---------------- điểm vào ----------------

def generate(prd: ParsedPRD, *, model: str, egress_dir: Path, transport: httpx.BaseTransport | None = None,
             policy: egress.EgressPolicy | None = None, source: str | None = None, timeout_s: float | None = None,
             auth: str | None = None) -> GenerateResult:
    """Một lời gọi LLM (+ tối đa MỘT lần sửa khi `bad_output`) -> catalog `draft`. Lỗi luôn là `GTError`.

    `source` là chuỗi ghi vào `prd.source` của catalog (CLI truyền đường dẫn tương đối của PRD); mặc định là `prd.prd_id`.
    Lần gọi thứ hai cũng ghi egress (mỗi request rời máy là một dòng).
    """
    total = sum(len(story.acs) for story in prd.stories)
    if total == 0:
        raise GTError("no_acs", "PRD không có acceptance criteria nào để sinh test case")  # chưa gửi gì đi, không ghi egress
    version, system = load_prompt()
    schema = tool_schema()
    timeout = timeout_s if timeout_s is not None else max(settings.get().llm_timeout_s, MIN_TIMEOUT_S)

    def ask(repair: str | None) -> llm.ToolCall:
        return llm.call_tool(purpose="gt-generate", model=model, system=system, user=build_user(prd, repair=repair, auth=auth),
                             tool_name=TOOL_NAME, tool_description=TOOL_DESCRIPTION, input_schema=schema,
                             egress_dir=egress_dir, data_categories=DATA_CATEGORIES, max_tokens=MAX_TOKENS,
                             timeout_s=timeout, policy=policy, transport=transport)

    cache_dir = settings.get().resolved_gt_cache_dir
    cache_key = gt_cache.make_key(prd=prd, model=model, prompt_version=version, auth=auth) if cache_dir is not None else None
    hit = gt_cache.lookup(cache_dir, cache_key, schema) if cache_key else None
    if cache_key:
        event(log, "gt.cache", logging.INFO, outcome="hit" if hit else "miss", key=cache_key[:8])

    fallback = False
    calls: list[CallRecord] = []
    if hit:
        data, attempts, used_model = hit["data"], hit["attempts"], model
        calls.append(CallRecord("gt-generate", model, version, "ok", hit["usage"], cache_hit=True))
    else:
        cap = settings.get().llm_max_input_tokens
        estimate = llm.estimate_input_tokens(system, build_user(prd, auth=auth), schema)
        if estimate > cap:   # ước lượng GẦN ĐÚNG (không phải trần cứng): chốt chặn chi phí, chưa gửi gì nên không có dòng usage
            event(log, "gt.token_cap", logging.WARNING, estimate=estimate, cap=cap, acs=total)
            raise GTError("token_cap", f"đầu vào ước lượng ~{estimate} token vượt QC_LLM_MAX_INPUT_TOKENS={cap} (ước lượng gần đúng): "
                                       "tách PRD thành nhiều file nhỏ hơn (mỗi file một nhóm story) rồi chạy từng file")

        def attempt(repair: str | None) -> llm.ToolCall:
            try:
                call = ask(repair)
            except llm.LLMError as error:
                if error.usage is not None or error.unknown_calls > 0:   # chưa gửi, hoặc API trả lỗi HTTP ([Assumption, chưa kiểm chứng] không tính phí): không có dòng
                    calls.append(CallRecord("gt-generate", model, version, error.kind, error.usage, error.duration_s, error.unknown_calls))
                raise
            calls.append(CallRecord("gt-generate", call.model, version, "ok", call.usage, call.duration_s))
            return call

        attempts = 1
        try:
            call = attempt(None)
        except llm.LLMError as first:
            if first.kind != "bad_output":
                raise GTError(first.kind, str(first), calls=tuple(calls)) from None
            attempts = 2
            try:
                call = attempt(str(first)[:400])   # vị trí + từ khoá vi phạm, do client dựng: không chứa nội dung PRD hay response
            except llm.LLMError as second:
                raise GTError(second.kind, f"{second} (sau 1 lần sửa)", calls=tuple(calls)) from None
        data, fallback = call.data, bool(call.fallback_from)
        used_model = call.model if fallback else model
    usage = total_usage(calls)

    catalog, warnings, orphans, dropped, merged = _assemble(prd, data, model=used_model, version=version, source=source or prd.prd_id)
    problems = gt_schema.validate_catalog(catalog)
    if problems:   # lỗi lập trình (conversion sinh catalog sai schema), không phải lỗi của LLM
        raise GTError("bad_output", "catalog sinh ra vi phạm schema: " + "; ".join(problems[:3]), calls=tuple(calls))   # lời gọi đã tốn dù catalog hỏng
    if cache_key and not hit and not fallback:   # chỉ output ĐÃ validate (cả schema tool lẫn catalog) của lời gọi thành công, đúng model đã yêu cầu
        stored = gt_cache.store(cache_dir, cache_key, data, usage, attempts)
        event(log, "gt.cache", logging.INFO, outcome="store" if stored else "store_failed", key=cache_key[:8])
    event(log, "gt.generate", logging.INFO, stories=len(prd.stories), acs=total, test_cases=len(catalog["test_cases"]), orphans=len(orphans),
          uncovered=len(catalog["uncovered_acs"]), dropped=dropped, merged=merged, attempts=attempts, cache_hit=bool(hit),
          unknown_calls=sum(c.unknown_calls for c in calls),
          input_tokens=usage.input_tokens, output_tokens=usage.output_tokens,
          cache_creation_input_tokens=usage.cache_creation_input_tokens, cache_read_input_tokens=usage.cache_read_input_tokens)
    return GenerateResult(catalog=catalog, usage=usage, warnings=tuple(warnings), orphans=tuple(orphans), dropped=dropped, cache_hit=bool(hit), calls=tuple(calls))
