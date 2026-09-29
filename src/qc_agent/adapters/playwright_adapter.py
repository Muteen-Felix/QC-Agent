"""Playwright integration adapter: one Playwright test title becomes one deterministic check."""
from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path
from urllib.parse import urlsplit

from qc_agent.adapters._base import Adapter, AdapterParseError, ParsedOutput

PARSER_VERSION = "1"
REPORT_NAME = "pw.json"
EVENTS_NAME = "events.json"
STDOUT_NAME = "stdout.log"
_CHECK = re.compile(r"[a-z][a-z0-9_]*")
_HOST = re.compile(r"[A-Za-z0-9](?:[A-Za-z0-9.-]{0,251}[A-Za-z0-9])?")
_TEST_CALL = re.compile(r"\btest\s*\(\s*(['\"])(.*?)\1", re.DOTALL)


def _repo_file(value: object, what: str, *, suffix: str | None = None) -> Path:
    if not isinstance(value, str) or not value or "\\" in value:
        raise AdapterParseError(f"inputs.{what} phải là đường dẫn tương đối không rỗng")
    path = Path(value)
    if path.is_absolute() or ".." in path.parts or (suffix and not value.endswith(suffix)):
        raise AdapterParseError(f"inputs.{what} không hợp lệ: {value!r}")
    root, resolved = Path.cwd().resolve(), (Path.cwd() / path).resolve()
    if resolved != root and root not in resolved.parents:
        raise AdapterParseError(f"inputs.{what} trỏ ra ngoài SUT root")
    if not resolved.is_file():
        raise AdapterParseError(f"không tìm thấy inputs.{what}: {value}")
    return resolved


def _http_host(value: object, what: str) -> str:
    parsed = urlsplit(value) if isinstance(value, str) else None
    if not parsed or parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username or parsed.password:
        raise AdapterParseError(f"{what} phải là URL HTTP(S) tuyệt đối không chứa thông tin đăng nhập")
    return parsed.hostname.lower()


def _host(value: object, what: str) -> str:
    if not isinstance(value, str) or not _HOST.fullmatch(value) or ":" in value:
        raise AdapterParseError(f"inputs.{what} phải là hostname hợp lệ")
    return value.lower()


def _lint_spec(path: Path) -> list[str]:
    try:
        text = path.read_text(encoding="utf-8-sig")
    except (OSError, UnicodeError) as error:
        raise AdapterParseError(f"không đọc được spec Playwright: {error}") from error
    if re.search(r"\brouteFromHAR\s*\(", text):
        raise AdapterParseError("spec không được tự gọi routeFromHAR; phải dùng ./support.mjs")
    if re.search(r"\bupdate\s*:\s*true\b", text):
        raise AdapterParseError("spec không được bật update: true")
    imports = re.findall(r"\bfrom\s*(['\"])(.*?)\1", text)
    modules = [module for _, module in imports]
    if any(module in {"playwright", "@playwright/test"} or module.startswith("playwright/") for module in modules):
        raise AdapterParseError("spec không được import Playwright trực tiếp; phải dùng ./support.mjs")
    if "./support.mjs" not in modules:
        raise AdapterParseError("spec phải import test/expect từ './support.mjs'")
    titles = [match[1] for match in _TEST_CALL.findall(text)]
    if not titles:
        raise AdapterParseError("spec không có test('check_name', ...)")
    invalid = [title for title in titles if not _CHECK.fullmatch(title)]
    if invalid:
        raise AdapterParseError(f"tên test phải là snake_case: {invalid[0]!r}")
    if len(titles) != len(set(titles)):
        raise AdapterParseError("tên test/check không được trùng")
    return titles


def _iter_specs(node: object):
    if isinstance(node, dict):
        specs = node.get("specs")
        if isinstance(specs, list):
            for spec in specs:
                if isinstance(spec, dict):
                    yield spec
        suites = node.get("suites")
        if isinstance(suites, list):
            for suite in suites:
                yield from _iter_specs(suite)


def _safe_path(value: object) -> str:
    if not isinstance(value, str):
        return "(không rõ)"
    try:
        path = urlsplit(value).path or "/"
        return path if re.fullmatch(r"/[A-Za-z0-9/._~%+-]*", path) else "(đường dẫn đã ẩn)"
    except ValueError:
        return "(không đọc được)"


class PlaywrightAdapter(Adapter):
    NAME = "playwright"
    ADAPTER_VERSION = "0.1.0"

    def __init__(self):
        super().__init__()
        self.env = {}

    def build_cmd(self, spec: dict, workdir: Path) -> list[str]:
        inputs = spec.get("inputs")
        if not isinstance(inputs, dict):
            raise AdapterParseError("inputs phải là object")
        spec_path = _repo_file(inputs.get("spec_file"), "spec_file", suffix=".spec.mjs")
        _lint_spec(spec_path)
        allowed = {_http_host(spec.get("target", {}).get("base_url"), "target.base_url")}
        ui_url = self.env.get("APP_UI_URL") or __import__("os").environ.get("APP_UI_URL")
        if ui_url:
            allowed.add(_http_host(ui_url, "APP_UI_URL"))
        extra = inputs.get("allow_hosts", [])
        if not isinstance(extra, list):
            raise AdapterParseError("inputs.allow_hosts phải là danh sách hostname")
        allowed.update(_host(item, "allow_hosts") for item in extra)

        har_value, b_value = inputs.get("har"), inputs.get("b_host")
        har_path = ""
        if har_value is not None:
            har_path = str(_repo_file(har_value, "har", suffix=".har"))
            if b_value is None:
                raise AdapterParseError("inputs.har cần inputs.b_host")
        b_host = _host(b_value, "b_host") if b_value is not None else ""
        if not har_path and b_host:
            allowed.add(b_host)  # tầng 3: B thật được phép nhưng không được replay

        report, events = (workdir / REPORT_NAME).resolve(), (workdir / EVENTS_NAME).resolve()
        report.unlink(missing_ok=True)
        events.unlink(missing_ok=True)
        self.env = {**self.env, "QC_ALLOW_HOSTS": ",".join(sorted(allowed)), "QC_HAR_FILE": har_path,
                    "QC_B_HOST": b_host, "QC_EVENTS_FILE": str(events),
                    "PLAYWRIGHT_JSON_OUTPUT_NAME": str(report)}
        npx = shutil.which("npx")
        if not npx:
            raise AdapterParseError("không tìm thấy npx trong PATH")
        # Playwright coi positional arg là regex; absolute Windows path có backslash nên không match test nào.
        return [npx, "playwright", "test", Path(inputs["spec_file"]).as_posix(), "--reporter=json", "--workers=1"]

    def parse_output(self, proc: subprocess.CompletedProcess, workdir: Path, spec: dict) -> ParsedOutput:
        report_path, events_path = workdir / REPORT_NAME, workdir / EVENTS_NAME
        if not report_path.is_file():
            raise AdapterParseError("Playwright không tạo pw.json")
        if not events_path.is_file():
            raise AdapterParseError("guard không tạo events.json")
        try:
            report = json.loads(report_path.read_text(encoding="utf-8-sig"))
            events = json.loads(events_path.read_text(encoding="utf-8-sig"))
        except (OSError, UnicodeError, ValueError) as error:
            raise AdapterParseError(f"không đọc được output Playwright: {error}") from error
        if not isinstance(report, dict) or not isinstance(report.get("suites"), list):
            raise AdapterParseError("pw.json thiếu suites[]")
        if not isinstance(events, dict) or not isinstance(events.get("blocked_hosts"), list) or not isinstance(events.get("har_missing"), list):
            raise AdapterParseError("events.json phải có blocked_hosts[] và har_missing[]")

        checks: dict[str, bool] = {}
        for item in _iter_specs(report):
            title, tests = item.get("title"), item.get("tests")
            if not isinstance(title, str) or not _CHECK.fullmatch(title) or not isinstance(tests, list) or not tests:
                continue
            results = [result for test in tests if isinstance(test, dict) for result in test.get("results", []) if isinstance(result, dict)]
            ran = [result for result in results if result.get("status") not in {"skipped", "interrupted"}]
            if ran:
                checks[title] = all(result.get("status") == "passed" for result in ran)
        blocked = sorted({host.lower() for host in events["blocked_hosts"] if isinstance(host, str) and _HOST.fullmatch(host)})
        missing = sorted({_safe_path(path) for path in events["har_missing"]})
        checks["no_external_requests"] = not blocked
        if spec.get("inputs", {}).get("har") is not None:
            checks["har_covers_all_requests"] = not missing

        findings = []
        if blocked:
            findings.append(_finding("external", "Host ngoài allowlist bị chặn: " + ", ".join(blocked[:10])))
        if missing:
            findings.append(_finding("har", "HAR thiếu request: " + ", ".join(missing[:10])))
        stdout_path = workdir / STDOUT_NAME
        stdout_path.write_text((proc.stdout or "") + ("\n" + proc.stderr if proc.stderr else ""), encoding="utf-8")
        return ParsedOutput(signals={"checks": checks}, findings=findings,
                            evidence_paths=[("raw_output", report_path), ("raw_output", events_path), ("stdout", stdout_path)],
                            tokens=0, usd=0.0, exit_code=proc.returncode,
                            adapter_notes=[f"PARSER_VERSION={PARSER_VERSION}", f"playwright exit_code={proc.returncode}"])


def _finding(kind: str, title: str) -> dict:
    return {"finding_id": f"f-pw-{kind}", "title": title[:300], "detected_by": f"playwright:{kind}",
            "verdict_source": "deterministic_assert", "confidence": None, "severity_hint": "high"}


if __name__ == "__main__":
    raise SystemExit(PlaywrightAdapter().main())
