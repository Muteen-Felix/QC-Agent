"""Translate a Schemathesis JUnit report into the shared checks oracle signals."""
from __future__ import annotations

import re
import shlex
import subprocess
import xml.etree.ElementTree as ET
from pathlib import Path
from urllib.parse import urlsplit

from adapters._base import Adapter, AdapterParseError, ParsedOutput


PARSER_VERSION = "1"
REPORT_NAME = "schemathesis.junit.xml"
STDOUT_NAME = "stdout.log"
CONFIG_NAME = "schemathesis.toml"
KNOWN_CHECKS = {"not_a_server_error", "response_schema_conformance"}
OPERATIONS_RE = re.compile(r"(?m)^\s*Operations:\s*(\d+)\s+selected\s*/\s*\d+\s+total\s*$")


class SchemathesisAdapter(Adapter):
    NAME = "schemathesis"
    ADAPTER_VERSION = "0.1.0"

    def build_cmd(self, spec: dict, workdir: Path) -> list[str]:
        inputs = spec.get("inputs", {})
        checks = inputs.get("checks")
        required = spec.get("oracle", {}).get("required")
        if not isinstance(checks, list) or not checks or any(not isinstance(item, str) for item in checks):
            raise AdapterParseError("inputs.checks debe ser una lista de checks no vacía")
        if not isinstance(required, list) or any(not isinstance(item, str) for item in required):
            raise AdapterParseError("oracle.required debe ser una lista de nombres de check")
        if len(set(checks)) != len(checks) or set(checks) != set(required):
            raise AdapterParseError("inputs.checks debe coincidir exactamente con oracle.required")
        unknown = sorted(set(checks) - KNOWN_CHECKS)
        if unknown:
            raise AdapterParseError(f"checks sin parser STEP 24: {unknown}")

        schema_url = inputs.get("schema_url")
        parsed_url = urlsplit(schema_url) if isinstance(schema_url, str) else None
        if not parsed_url or parsed_url.scheme not in {"http", "https"} or not parsed_url.netloc:
            raise AdapterParseError("inputs.schema_url debe ser una URL HTTP(S) absoluta")
        max_examples = inputs.get("max_examples")
        seed = inputs.get("seed")
        if isinstance(max_examples, bool) or not isinstance(max_examples, int) or max_examples < 1:
            raise AdapterParseError("inputs.max_examples debe ser un entero > 0")
        if isinstance(seed, bool) or not isinstance(seed, int):
            raise AdapterParseError("inputs.seed phải là số nguyên")
        exclude_path = inputs.get("exclude_path")
        if not isinstance(exclude_path, str) or not exclude_path.startswith("/"):
            raise AdapterParseError("inputs.exclude_path debe ser una ruta absoluta del API")

        report_path = (workdir / REPORT_NAME).resolve()
        report_path.unlink(missing_ok=True)
        config_path = (workdir / CONFIG_NAME).resolve()
        config_path.write_text("[cache]\nenabled = false\n", encoding="utf-8")
        return [
            "st", "--config-file", str(config_path), "run", schema_url,
            "--checks", ",".join(checks),
            "--max-examples", str(max_examples),
            "--seed", str(seed),
            "--exclude-path", exclude_path,
            "--generation-database", "none",
            "--report", "junit",
            "--report-junit-path", str(report_path),
            "--no-color",
        ]

    def parse_output(
        self, proc: subprocess.CompletedProcess, workdir: Path, spec: dict
    ) -> ParsedOutput:
        stdout = proc.stdout or ""
        stdout_path = workdir / STDOUT_NAME
        stdout_path.write_text(stdout, encoding="utf-8")
        report_path = workdir / REPORT_NAME
        if not report_path.is_file():
            raise AdapterParseError(f"không có JUnit report: {report_path.name}")
        try:
            root = ET.parse(report_path).getroot()
        except (OSError, ET.ParseError) as error:
            raise AdapterParseError(f"JUnit report không đọc được: {error}") from error

        local_name = root.tag.rsplit("}", 1)[-1]
        if local_name not in {"testsuite", "testsuites"}:
            raise AdapterParseError(f"JUnit root không được hỗ trợ: {local_name!r}")
        testcases = [element for element in root.iter() if element.tag.rsplit("}", 1)[-1] == "testcase"]
        errors = [element for element in root.iter() if element.tag.rsplit("}", 1)[-1] == "error"]
        if errors:
            raise AdapterParseError(f"JUnit report có {len(errors)} error ngoài check")
        failures = [element for element in root.iter() if element.tag.rsplit("}", 1)[-1] == "failure"]

        # Schemathesis JUnit has one testcase per selected operation plus the optional stateful summary.
        # Cross-check declared XML counts and the CLI's operation count so a partial clean report cannot pass.
        junit_scopes = [
            element for element in root.iter()
            if element.tag.rsplit("}", 1)[-1] in {"testsuite", "testsuites"}
        ]
        for scope in junit_scopes:
            descendants = list(scope.iter())
            for attribute, tag_name in (("tests", "testcase"), ("failures", "failure"), ("errors", "error")):
                declared = scope.attrib.get(attribute)
                if declared is None:
                    raise AdapterParseError(f"JUnit {scope.tag!r} thiếu thuộc tính {attribute}")
                try:
                    declared_count = int(declared)
                except ValueError as error:
                    raise AdapterParseError(f"JUnit có {attribute} không phải số") from error
                actual_count = sum(element.tag.rsplit("}", 1)[-1] == tag_name for element in descendants)
                if declared_count != actual_count:
                    raise AdapterParseError(
                        f"JUnit {attribute}={declared_count} nhưng nội dung có {actual_count} {tag_name}"
                    )

        operation_cases = [case for case in testcases if case.attrib.get("name") != "Stateful tests"]
        if not operation_cases:
            raise AdapterParseError("JUnit report không có testcase API nào đã chạy")
        operation_match = OPERATIONS_RE.search(stdout)
        if operation_match is None:
            raise AdapterParseError("stdout thiếu tổng số operation đã chọn của Schemathesis")
        expected_operations = int(operation_match.group(1))
        if expected_operations < 1 or len(operation_cases) != expected_operations:
            raise AdapterParseError(
                f"JUnit chưa đủ operation testcase: có {len(operation_cases)}, cần {expected_operations}"
            )
        if any(
            any(child.tag.rsplit("}", 1)[-1] == "skipped" for child in testcase)
            for testcase in operation_cases
        ):
            raise AdapterParseError("JUnit có operation testcase bị skipped")
        operation_names = [case.attrib.get("name", "") for case in operation_cases]
        if any(not name for name in operation_names) or len(set(operation_names)) != len(operation_names):
            raise AdapterParseError("JUnit có tên operation testcase thiếu hoặc trùng")

        required = spec.get("oracle", {}).get("required")
        if (
            not isinstance(required, list) or not required
            or any(not isinstance(name, str) or name not in KNOWN_CHECKS for name in required)
        ):
            raise AdapterParseError("oracle.required thiếu hoặc có check chưa được hỗ trợ")

        checks = {name: True for name in required}
        case_of = {
            child: case for case in testcases for child in case
            if child.tag.rsplit("}", 1)[-1] == "failure"
        }
        findings = []
        for failure in failures:
            detail = "".join(failure.itertext())
            lowered = detail.lower()
            if "not_a_server_error" in lowered:
                failed_check = "not_a_server_error"
            elif "response_schema_conformance" in lowered or "response violates schema" in lowered:
                failed_check = "response_schema_conformance"
            elif "- server error" in lowered and any(f"[{status}]" in lowered for status in range(500, 600)):
                failed_check = "not_a_server_error"
            else:
                raise AdapterParseError(f"JUnit failure không quy được về check đã biết: {detail[:240]!r}")
            if failed_check not in required:
                raise AdapterParseError(f"JUnit có lỗi ở check không được yêu cầu: {failed_check}")
            checks[failed_check] = False
            name = case_of[failure].attrib.get("name", "?")
            findings.append({
                "finding_id": f"f-st-{failed_check}-{len(findings) + 1}",
                "title": f"{name}: {failed_check} không đạt",
                "detected_by": f"schemathesis:{failed_check}",
                "verdict_source": "deterministic_assert",
            })

        if (proc.returncode != 0) != bool(failures):
            raise AdapterParseError("mâu thuẫn exit code/báo cáo")

        args = proc.args if isinstance(proc.args, (list, tuple)) else [str(proc.args)]
        return ParsedOutput(
            findings=findings,
            signals={"checks": checks},
            evidence_paths=[("raw_output", report_path), ("stdout", stdout_path)],
            tokens=0,
            usd=0.0,
            exit_code=proc.returncode,
            replay_cmd=shlex.join([str(argument) for argument in args]),
            adapter_notes=[f"PARSER_VERSION={PARSER_VERSION}"],
        )


if __name__ == "__main__":
    raise SystemExit(SchemathesisAdapter().main())
