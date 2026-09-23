"""Midscene CLI adapter. Parser version 1 is based on STEP 23 samples."""
import json
import re
import shutil
from pathlib import Path

import httpx

from adapters._base import Adapter, AdapterParseError, ParsedOutput

PARSER_VERSION = "1"
_ELEMENT_NOT_FOUND = re.compile(r"kh[oô]ng t[iì]m th[aấ]y|not found|unable to find|could not find", re.IGNORECASE)
_INFRA_ERROR = re.compile(
    r"\b429\b|resource_exhausted|model request failed|model configuration is incomplete|"
    r"xml parse error|incomplete planning response|timed out|timeout",
    re.IGNORECASE,
)


def _reset_events(base_url: str) -> None:
    try:
        response = httpx.delete(base_url.rstrip("/") + "/__qc/events", timeout=10)
        response.raise_for_status()
    except httpx.HTTPError as exc:
        raise AdapterParseError(f"không xoá được telemetry: {exc}") from exc


def _events(base_url: str) -> list:
    try:
        response = httpx.get(base_url.rstrip("/") + "/__qc/events", timeout=10)
        response.raise_for_status()
        data = response.json()
    except (httpx.HTTPError, ValueError) as exc:
        raise AdapterParseError(f"không đọc được telemetry: {exc}") from exc
    if not isinstance(data, list):
        raise AdapterParseError("telemetry không phải danh sách")
    return data


class MidsceneAdapter(Adapter):
    NAME = "midscene-cli"
    ADAPTER_VERSION = "0.1.0"

    def build_cmd(self, spec: dict, workdir: Path) -> list[str]:
        _reset_events(spec["target"]["base_url"])
        (workdir / "summary.json").unlink(missing_ok=True)
        npx = shutil.which("npx")
        if not npx:
            raise AdapterParseError("không tìm thấy npx trong PATH")
        return [npx, "@midscene/cli", spec["inputs"]["flow"], "--summary", str(workdir / "summary.json")]

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

        events = _events(spec["target"]["base_url"])
        events_path = workdir / "events.json"
        events_path.write_text(json.dumps(events, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        stdout_path = workdir / "stdout.log"
        stdout_path.write_text(proc.stdout or "", encoding="utf-8")

        detected = []
        if flow_failed and _ELEMENT_NOT_FOUND.search(errors):
            detected.append({"name": "element_not_found", "title": errors.strip().splitlines()[0][:200]})
        detected.extend(_telemetry_signals(events, spec))

        notes = ["PARSER_VERSION=1", "aiAssert không có confidence → không phát finding llm_judgment"]
        if data.get("_fixture_notice"):
            notes.append("⚠ MOCK: cấu trúc summary pass chưa xác nhận với Midscene thật")
        return ParsedOutput(
            signals={"detected": detected},
            evidence_paths=[("raw_output", summary_path), ("raw_output", events_path), ("stdout", stdout_path)],
            tokens=_reported_number(data, "tokens"),
            usd=_reported_number(data, "usd"),
            exit_code=proc.returncode,
            flow_failed=flow_failed,
            adapter_notes=notes,
        )


def _telemetry_signals(events: list, spec: dict) -> list:
    detected = []
    types = [event.get("type") for event in events if isinstance(event, dict)]
    delete_positions = [index for index, event_type in enumerate(types) if event_type == "delete_clicked"]
    if delete_positions and "render_done" not in types[delete_positions[-1] + 1:]:
        hint = (spec["inputs"].get("promote_hints") or {}).get("dom_unchanged")
        signal = {"name": "dom_unchanged", "title": "DOM không render lại sau thao tác xoá"}
        if hint:
            signal["promote_candidate"] = {"suggested_capability": spec["capability"], **hint}
        detected.append(signal)
    if "console_error" in types:
        detected.append({"name": "console_error"})
    if "http_5xx" in types:
        detected.append({"name": "http_5xx"})
    return detected


def _reported_number(data: dict, key: str):
    value = data.get(key)
    return value if isinstance(value, (int, float)) and not isinstance(value, bool) else None


if __name__ == "__main__":
    raise SystemExit(MidsceneAdapter().main())
