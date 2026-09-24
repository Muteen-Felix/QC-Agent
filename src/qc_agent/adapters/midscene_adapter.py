"""Midscene CLI adapter. Parser version 2: URL từ target, max_steps, telemetry là plugin tuỳ chọn (inputs.telemetry)."""
import json
import re
import shutil
from pathlib import Path
from urllib.parse import urlsplit

import httpx
import yaml

from qc_agent.adapters._base import Adapter, AdapterParseError, ParsedOutput

PARSER_VERSION = "2"
TELEMETRY_KEYS = {"endpoint", "console_error", "http_5xx", "dom_unchanged"}
_ELEMENT_NOT_FOUND = re.compile(r"kh[oô]ng t[iì]m th[aấ]y|not found|unable to find|could not find", re.IGNORECASE)
_INFRA_ERROR = re.compile(
    r"\b429\b|resource_exhausted|model request failed|model configuration is incomplete|"
    r"xml parse error|incomplete planning response|timed out|timeout",
    re.IGNORECASE,
)


def _reset_events(base_url: str, endpoint: str) -> None:
    try:
        response = httpx.delete(base_url.rstrip("/") + endpoint, timeout=10)
        response.raise_for_status()
    except httpx.HTTPError as exc:
        raise AdapterParseError(f"không xoá được telemetry: {exc}") from exc


def _events(base_url: str, endpoint: str) -> list:
    try:
        response = httpx.get(base_url.rstrip("/") + endpoint, timeout=10)
        response.raise_for_status()
        data = response.json()
    except (httpx.HTTPError, ValueError) as exc:
        raise AdapterParseError(f"không đọc được telemetry: {exc}") from exc
    if not isinstance(data, list):
        raise AdapterParseError("telemetry không phải danh sách")
    return data


def _relative_path(value) -> bool:
    return isinstance(value, str) and value.startswith("/") and not value.startswith("//") and "://" not in value and "\\" not in value


def validate_telemetry(telemetry) -> list[str]:
    """inputs.telemetry (plugin tuỳ chọn): {endpoint, console_error?, http_5xx?, dom_unchanged?: {after, expect}}; giá trị là TÊN loại sự kiện."""
    if not isinstance(telemetry, dict):
        return ["inputs.telemetry phải là object"]
    errors = []
    if set(telemetry) - TELEMETRY_KEYS:
        errors.append(f"inputs.telemetry có khoá lạ: {sorted(set(telemetry) - TELEMETRY_KEYS)}")
    if not _relative_path(telemetry.get("endpoint")):
        errors.append("inputs.telemetry.endpoint phải là đường dẫn bắt đầu bằng một dấu '/'")
    for key in ("console_error", "http_5xx"):
        if key in telemetry and (not isinstance(telemetry[key], str) or not telemetry[key]):
            errors.append(f"inputs.telemetry.{key} phải là tên loại sự kiện")
    rule = telemetry.get("dom_unchanged")
    if rule is not None and (not isinstance(rule, dict) or set(rule) != {"after", "expect"}
                             or not all(isinstance(v, str) and v for v in rule.values())):
        errors.append("inputs.telemetry.dom_unchanged phải là {after: <sự kiện>, expect: <sự kiện>}")
    return errors


def _entry_url(target: dict) -> str:
    base_url, entry = target.get("base_url"), target.get("entry_path", "/")
    parsed = urlsplit(base_url) if isinstance(base_url, str) else None
    if not parsed or parsed.scheme not in {"http", "https"} or not parsed.netloc or parsed.username or parsed.password:
        raise AdapterParseError("target.base_url phải là URL HTTP(S) tuyệt đối, không chứa thông tin đăng nhập")
    if not _relative_path(entry):
        raise AdapterParseError("target.entry_path phải là đường dẫn bắt đầu bằng một dấu '/'")
    return base_url.rstrip("/") + entry


class MidsceneAdapter(Adapter):
    NAME = "midscene-cli"
    ADAPTER_VERSION = "0.2.0"

    def __init__(self):
        super().__init__()
        self.env = {}

    def build_cmd(self, spec: dict, workdir: Path) -> list[str]:
        inputs = spec["inputs"]
        url = _entry_url(spec["target"])  # URL đến từ target, KHÔNG từ flow: cùng một flow chạy được trên môi trường nào cũng được
        problems = validate_telemetry(inputs["telemetry"]) if "telemetry" in inputs else []
        max_steps = inputs.get("max_steps")
        if max_steps is not None and (isinstance(max_steps, bool) or not isinstance(max_steps, int) or max_steps < 1):
            problems.append("inputs.max_steps phải là số nguyên >= 1")
        if problems:
            raise AdapterParseError(problems[0])
        try:
            flow = yaml.safe_load(Path(inputs["flow"]).read_text(encoding="utf-8-sig"))
        except (OSError, yaml.YAMLError) as exc:
            raise AdapterParseError(f"không đọc được flow: {exc}") from exc
        if not isinstance(flow, dict) or not isinstance(flow.get("tasks"), list) or not isinstance(flow.get("web", {}), dict):
            raise AdapterParseError("flow phải là object có tasks[] (và web là object nếu có)")
        flow["web"] = {**flow.get("web", {}), "url": url}
        rendered = workdir / "flow.yaml"
        rendered.write_text(yaml.safe_dump(flow, allow_unicode=True, sort_keys=False), encoding="utf-8")
        if "telemetry" in inputs:
            _reset_events(spec["target"]["base_url"], inputs["telemetry"]["endpoint"])
        if max_steps is not None:
            # Midscene không có "max_steps" theo nghĩa từng bước; giới hạn gần nhất là số vòng lập kế hoạch lại của mỗi aiAction.
            self.env["MIDSCENE_REPLANNING_CYCLE_LIMIT"] = str(max_steps)
        (workdir / "summary.json").unlink(missing_ok=True)
        npx = shutil.which("npx")
        if not npx:
            raise AdapterParseError("không tìm thấy npx trong PATH")
        return [npx, "@midscene/cli", str(rendered), "--summary", str(workdir / "summary.json")]

    def parse_output(self, proc, workdir: Path, spec: dict) -> ParsedOutput:
        summary_path = workdir / "summary.json"
        if not summary_path.is_file():
            raise AdapterParseError("Midscene không tạo summary.json")
        try:
            data = json.loads(summary_path.read_text(encoding="utf-8-sig"))
        except (OSError, ValueError) as exc:
            raise AdapterParseError(f"không đọc được summary.json: {exc}") from exc

        results = data.get("results")
        if not isinstance(results, list) or not results:
            raise AdapterParseError("summary thiếu results[]")
        flow_failed = any(result.get("success") is not True for result in results)
        errors = "\n".join(str(result.get("error") or "") for result in results)
        if flow_failed and _INFRA_ERROR.search(errors):
            raise AdapterParseError(f"Midscene lỗi hạ tầng: {errors[:300]}")
        if (proc.returncode == 0 and flow_failed) or (proc.returncode != 0 and not flow_failed):
            raise AdapterParseError("mâu thuẫn exit code/summary")

        telemetry = spec["inputs"].get("telemetry")
        evidence = [("raw_output", summary_path)]
        events: list = []
        if telemetry:
            events = _events(spec["target"]["base_url"], telemetry["endpoint"])
            events_path = workdir / "events.json"
            events_path.write_text(json.dumps(events, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            evidence.append(("raw_output", events_path))
        stdout_path = workdir / "stdout.log"
        stdout_path.write_text(proc.stdout or "", encoding="utf-8")

        detected = []
        if flow_failed and _ELEMENT_NOT_FOUND.search(errors):
            detected.append({"name": "element_not_found", "title": errors.strip().splitlines()[0][:200]})
        detected.extend(_telemetry_signals(events, spec, telemetry or {}))

        notes = [f"PARSER_VERSION={PARSER_VERSION}", "telemetry: " + ("bật" if telemetry else "tắt (chỉ tín hiệu từ summary)"),
                 "aiAssert không có confidence → không phát finding llm_judgment"]
        if data.get("_fixture_notice"):
            notes.append("⚠ MOCK: cấu trúc summary pass chưa xác nhận với Midscene thật")
        return ParsedOutput(
            signals={"detected": detected},
            evidence_paths=[*evidence, ("stdout", stdout_path)],
            tokens=_reported_number(data, "tokens"),
            usd=_reported_number(data, "usd"),
            exit_code=proc.returncode,
            flow_failed=flow_failed,
            adapter_notes=notes,
        )


def _telemetry_signals(events: list, spec: dict, telemetry: dict) -> list:
    detected = []
    types = [event.get("type") for event in events if isinstance(event, dict)]
    rule = telemetry.get("dom_unchanged")
    if rule:
        after_positions = [index for index, event_type in enumerate(types) if event_type == rule["after"]]
        if after_positions and rule["expect"] not in types[after_positions[-1] + 1:]:
            hint = (spec["inputs"].get("promote_hints") or {}).get("dom_unchanged")
            signal = {"name": "dom_unchanged", "title": f"DOM không cập nhật sau sự kiện '{rule['after']}'"}
            if hint:
                signal["promote_candidate"] = {"suggested_capability": spec["capability"], **hint}
            detected.append(signal)
    for name in ("console_error", "http_5xx"):
        if telemetry.get(name) and telemetry[name] in types:
            detected.append({"name": name})
    return detected


def _reported_number(data: dict, key: str):
    value = data.get(key)
    return value if isinstance(value, (int, float)) and not isinstance(value, bool) else None


if __name__ == "__main__":
    raise SystemExit(MidsceneAdapter().main())
