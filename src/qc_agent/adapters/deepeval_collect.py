"""Thu thập input/output của SUT qua HTTP theo mẫu khai báo trong `inputs.collect` (thay cho worker http-collect riêng của PoC).

Cấu hình (mọi thứ nằm trong suite của repo SUT, core không biết endpoint nào):

    collect:
      golden: tests/eval/golden.json        # danh sách case; mỗi case có `id` ([a-z0-9]+) và các trường tự do
      steps:                                # chạy tuần tự cho MỖI case; non-2xx dừng chuỗi bước của case đó
        - {id: create, method: POST, path: /notes, json: {title: "{{nonce}}-{{case.title}}", body: "{{case.body}}"}, capture: {note_id: id}}
        - {id: summarize, method: POST, path: "/notes/{{note_id}}/summarize", capture: {actual_output: summary}}
      cleanup:                              # tuỳ chọn, best-effort sau mỗi case (bỏ qua nếu biến chưa có)
        - {method: DELETE, path: "/notes/{{note_id}}"}
      record:                               # ánh xạ output: bắt buộc có `input` và `actual_output`
        input: "{{case.body}}"
        actual_output: "{{actual_output}}"

Mẫu dùng `{{...}}` (không dùng `${...}` vì engine đã dùng cho ${env.X}/${run_id}). Biến: `case.<trường>`, `nonce` (chuỗi ngẫu nhiên MỖI lần chạy:
dùng để đặt tên dữ liệu test riêng cho từng job, tránh đụng nhau khi chạy song song) và các biến `capture`. Đường dẫn chỉ được là đường dẫn
tương đối theo base_url (không URL tuyệt đối => không thể đổi đích ra ngoài target). Giá trị chèn vào `path` được percent-encode.
"""
from __future__ import annotations

import json
import re
import uuid
from pathlib import Path
from typing import Callable
from urllib.parse import quote, urlsplit

import httpx

OUTPUT_NAME = "outputs.json"
REPORT_NAME = "collection.json"
EXIT_COLLECT_FAILED = 3  # exit code của deepeval_worker khi thu thập không đáng tin
METHODS = {"GET", "POST", "PUT", "PATCH", "DELETE"}
CASE_ID = re.compile(r"[a-z0-9]+")
_VAR = re.compile(r"\{\{\s*([A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z0-9_]+)*)\s*\}\}")
REQUIRED_RECORD_FIELDS = ("input", "actual_output")


class CollectionError(Exception):
    """Việc thu thập không tạo ra kết quả đáng tin (cấu hình sai, transport lỗi, phản hồi sai dạng) => error, không phải fail."""


class _Missing(Exception):
    pass


def _dig(value, path: str):
    for part in path.split("."):
        if isinstance(value, dict) and part in value:
            value = value[part]
        elif isinstance(value, list) and part.isdigit() and int(part) < len(value):
            value = value[int(part)]
        else:
            raise _Missing(path)
    return value


def _text(value) -> str:
    return value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)


def render(template, variables: dict, *, url_path: bool = False):
    """Thay `{{biến}}` đệ quy. Chuỗi chỉ gồm đúng một biến giữ nguyên kiểu (int/dict...) — trừ khi là đường dẫn URL."""
    if isinstance(template, dict):
        return {key: render(item, variables) for key, item in template.items()}
    if isinstance(template, list):
        return [render(item, variables) for item in template]
    if not isinstance(template, str):
        return template
    whole = _VAR.fullmatch(template.strip())
    if whole and not url_path:
        return _dig(variables, whole.group(1))
    return _VAR.sub(lambda m: (quote(_text(_dig(variables, m.group(1))), safe="") if url_path else _text(_dig(variables, m.group(1)))), template)


def validate_config(collect) -> list[str]:
    """Kiểm tra hình dạng cấu hình; trả danh sách lỗi (rỗng = hợp lệ)."""
    if not isinstance(collect, dict):
        return ["inputs.collect phải là object"]
    errors: list[str] = []
    extra = set(collect) - {"golden", "steps", "cleanup", "record"}
    if extra:
        errors.append(f"inputs.collect có khoá lạ: {sorted(extra)}")
    if not isinstance(collect.get("golden"), str) or not collect["golden"].strip():
        errors.append("inputs.collect.golden phải là đường dẫn file")
    steps = collect.get("steps")
    if not isinstance(steps, list) or not steps:
        errors.append("inputs.collect.steps phải là danh sách không rỗng")
        steps = []
    cleanup = collect.get("cleanup", [])
    if not isinstance(cleanup, list):
        errors.append("inputs.collect.cleanup phải là danh sách")
        cleanup = []
    for label, group in (("steps", steps), ("cleanup", cleanup)):
        for index, step in enumerate(group):
            where = f"inputs.collect.{label}[{index}]"
            if not isinstance(step, dict):
                errors.append(f"{where} phải là object")
                continue
            if set(step) - {"id", "method", "path", "json", "capture"}:
                errors.append(f"{where} có khoá lạ: {sorted(set(step) - {'id', 'method', 'path', 'json', 'capture'})}")
            if str(step.get("method", "")).upper() not in METHODS:
                errors.append(f"{where}.method phải thuộc {sorted(METHODS)}")
            path = step.get("path")
            if not isinstance(path, str) or not path.startswith("/") or path.startswith("//") or "://" in path or "\\" in path:
                errors.append(f"{where}.path phải là đường dẫn bắt đầu bằng một dấu '/' (không nhận URL tuyệt đối)")
            capture = step.get("capture", {})
            if not isinstance(capture, dict) or any(not isinstance(k, str) or not re.fullmatch(r"[A-Za-z_]\w*", k) or not isinstance(v, str)
                                                    for k, v in capture.items()):
                errors.append(f"{where}.capture phải là {{tên_biến: đường.dẫn.trong.json}}")
    record = collect.get("record")
    if not isinstance(record, dict) or any(name not in record for name in REQUIRED_RECORD_FIELDS):
        errors.append(f"inputs.collect.record phải là object có {', '.join(REQUIRED_RECORD_FIELDS)}")
    elif any(name in {"id", "http_status"} or not isinstance(name, str) for name in record):
        errors.append("inputs.collect.record không được đặt lại 'id'/'http_status' (do bộ thu tự điền)")
    return errors


def _load_cases(golden_path: Path) -> list[dict]:
    try:
        cases = json.loads(golden_path.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError) as error:
        raise CollectionError(f"cannot read golden file: {type(error).__name__}") from error
    if not isinstance(cases, list) or not cases:
        raise CollectionError("golden file must be a non-empty list")
    for index, case in enumerate(cases):
        if not isinstance(case, dict) or not isinstance(case.get("id"), str) or not CASE_ID.fullmatch(case["id"]):
            raise CollectionError(f"golden case #{index + 1} needs an id matching [a-z0-9]+")
    if len({case["id"] for case in cases}) != len(cases):
        raise CollectionError("golden case ids must be unique")
    return cases


def collect(collect_config: dict, base_url: str, golden_path: Path, client_factory: Callable[..., httpx.Client] = httpx.Client,
            nonce: str | None = None) -> tuple[list[dict], dict]:
    problems = validate_config(collect_config)
    if problems:
        raise CollectionError(problems[0])
    parsed_url = urlsplit(base_url)
    if parsed_url.scheme not in {"http", "https"} or not parsed_url.netloc:
        raise CollectionError("base URL must be an absolute HTTP(S) URL")
    cases = _load_cases(golden_path)
    nonce = nonce or uuid.uuid4().hex[:8]
    records: list[dict] = []
    all_http_2xx = True
    transport_error: httpx.RequestError | None = None

    def call(client, step, variables) -> httpx.Response:
        try:
            path = render(step["path"], variables, url_path=True)
            body = render(step["json"], variables) if "json" in step else None
        except _Missing as error:
            raise CollectionError(f"step {step.get('id', step['path'])!r} refers to unknown variable {error.args[0]!r}") from error
        return client.request(step["method"].upper(), path, **({"json": body} if body is not None else {}))

    try:
        with client_factory(base_url=base_url.rstrip("/"), timeout=10.0) as client:
            for case in cases:
                variables: dict = {"case": case, "nonce": nonce}
                record = {"id": case["id"], "http_status": None}
                step_failed = False
                try:
                    for step in collect_config["steps"]:
                        response = call(client, step, variables)
                        record["http_status"] = response.status_code
                        all_http_2xx = response.is_success and all_http_2xx
                        if not response.is_success:
                            step_failed = True
                            break
                        if step.get("capture"):
                            try:
                                payload = response.json()
                            except ValueError as error:
                                raise CollectionError(f"step {step.get('id', step['path'])!r} returned invalid JSON") from error
                            for name, path in step["capture"].items():
                                try:
                                    variables[name] = _dig(payload, path)
                                except _Missing as error:
                                    raise CollectionError(f"step {step.get('id', step['path'])!r} response lacks {path!r}") from error
                    for name, template in collect_config["record"].items():
                        try:
                            record[name] = render(template, variables)
                        except _Missing as error:
                            if not step_failed:  # thiếu biến dù mọi bước đều 2xx => cấu hình/phản hồi sai, không được đoán
                                raise CollectionError(f"record.{name} refers to unknown variable {error.args[0]!r}") from error
                            record[name] = ""
                    records.append(record)
                finally:
                    for step in collect_config.get("cleanup", []):
                        try:
                            path = render(step["path"], variables, url_path=True)
                        except _Missing:
                            continue  # bước tạo dữ liệu chưa chạy tới: không có gì để dọn
                        try:
                            all_http_2xx = client.request(step["method"].upper(), path).is_success and all_http_2xx
                        except httpx.RequestError as error:
                            all_http_2xx = False
                            transport_error = transport_error or error
    except httpx.RequestError as error:
        transport_error = transport_error or error

    if transport_error is not None:
        raise CollectionError(f"HTTP transport error: {type(transport_error).__name__}") from transport_error
    return records, {
        "count": len(records),
        "expected_count": len(cases),
        "checks": {"all_http_2xx": bool(all_http_2xx), "count_matches_golden": len(records) == len(cases)},
    }


def write_collection_artifacts(records: list[dict], report: dict, output_path: Path, report_path: Path) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(records, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
