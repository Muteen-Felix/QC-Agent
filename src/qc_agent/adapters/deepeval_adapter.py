"""Convert DeepEval pytest/JUnit output into shared deterministic and advisory results."""
from __future__ import annotations

import json
import math
import os
import re
import shlex
import subprocess
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

from qc_agent.adapters._base import Adapter, AdapterParseError, ParsedOutput


PARSER_VERSION = "2"
JUNIT_NAME = "junit.xml"
GEVAL_NAME = "geval.json"
STDOUT_NAME = "stdout.log"
METRICS = (
    "summary_not_empty",
    "summary_shorter_than_body",
    "summary_json_valid",
)
METRIC_CASE_RE = re.compile(
    r"^test_metric\[(?P<metric>[a-z_]+)-(?P<case>[a-z0-9]+)\]$"
)


def _tag(element: ET.Element) -> str:
    return element.tag.rsplit("}", 1)[-1]


class DeepEvalAdapter(Adapter):
    NAME = "deepeval"
    ADAPTER_VERSION = "0.1.0"

    def __init__(self):
        super().__init__()
        self.env = {}

    def build_cmd(self, spec: dict, workdir: Path) -> list[str]:
        inputs = spec.get("inputs", {})
        judge_config = inputs.get("judge_config")
        if not isinstance(judge_config, dict):
            raise AdapterParseError("inputs.judge_config phải là object")
        primary_model = judge_config.get("model")
        sut_model = inputs.get("sut_model")
        if not isinstance(primary_model, str) or not primary_model.strip():
            raise AdapterParseError("inputs.judge_config.model phải là tên model")
        if not isinstance(sut_model, str) or not sut_model.strip():
            raise AdapterParseError("inputs.sut_model phải là tên model")
        configured_models = [primary_model]
        fallback_model = judge_config.get("fallback_model")
        if isinstance(fallback_model, str) and fallback_model.strip():
            configured_models.append(fallback_model)
        if any(model.strip() == sut_model.strip() for model in configured_models):
            raise AdapterParseError("judge trùng model của SUT")

        outputs_path = inputs.get("outputs_path")
        if not isinstance(outputs_path, str) or not outputs_path.strip():
            raise AdapterParseError("inputs.outputs_path phải là đường dẫn file")
        outputs_path = Path(outputs_path).resolve()
        if not outputs_path.is_file():
            raise AdapterParseError("QC_EVAL_OUTPUTS không tồn tại")

        report_path = (workdir / JUNIT_NAME).resolve()
        report_path.unlink(missing_ok=True)
        (workdir / GEVAL_NAME).unlink(missing_ok=True)
        out_dir = workdir.resolve()
        self.env.update(
            {
                "QC_EVAL_OUTPUTS": str(outputs_path),
                "QC_EVAL_OUT_DIR": str(out_dir),
                "DEEPEVAL_RESULTS_FOLDER": str(out_dir / "deepeval-results"),
                # Keep the declared data-egress boundary limited to the judge provider.
                "DEEPEVAL_TELEMETRY_OPT_OUT": "YES",
            }
        )
        for field, env_name in (
            ("provider", "QC_JUDGE_PROVIDER"),
            ("model", "QC_JUDGE_MODEL"),
            ("fallback_provider", "QC_JUDGE_FALLBACK_PROVIDER"),
            ("fallback_model", "QC_JUDGE_FALLBACK_MODEL"),
        ):
            value = judge_config.get(field)
            if value is not None:
                if not isinstance(value, str):
                    raise AdapterParseError(f"judge_config.{field} phải là chuỗi")
                self.env[env_name] = value

        python = os.environ.get("QC_DEEPEVAL_PYTHON") or sys.executable
        test_path = Path("tests/eval/test_summarize.py").resolve()
        return [
            python,
            "-m",
            "pytest",
            str(test_path),
            f"--junitxml={report_path}",
            "-q",
            "-p",
            "no:cacheprovider",
        ]

    def parse_output(
        self, proc: subprocess.CompletedProcess, workdir: Path, spec: dict
    ) -> ParsedOutput:
        stdout_path = workdir / STDOUT_NAME
        stdout_path.write_text(proc.stdout or "", encoding="utf-8")
        report_path = workdir / JUNIT_NAME
        if not report_path.is_file():
            raise AdapterParseError("không có JUnit report")
        try:
            root = ET.parse(report_path).getroot()
        except (OSError, ET.ParseError) as error:
            raise AdapterParseError("JUnit report không đọc được") from error
        if _tag(root) not in {"testsuite", "testsuites"}:
            raise AdapterParseError("JUnit root không được hỗ trợ")
        for scope in root.iter():
            if _tag(scope) not in {"testsuite", "testsuites"}:
                continue
            descendants = list(scope.iter())
            for attribute, child_tag in (
                ("tests", "testcase"),
                ("failures", "failure"),
                ("errors", "error"),
            ):
                declared = scope.attrib.get(attribute)
                if declared is None:
                    continue
                try:
                    declared_count = int(declared)
                except ValueError as error:
                    raise AdapterParseError(f"JUnit {attribute} không phải số") from error
                actual_count = sum(_tag(element) == child_tag for element in descendants)
                if declared_count != actual_count:
                    raise AdapterParseError(
                        f"JUnit {attribute}={declared_count} nhưng nội dung có {actual_count} {child_tag}"
                    )

        try:
            raw_records = json.loads(
                Path(spec["inputs"]["outputs_path"]).read_text(encoding="utf-8-sig")
            )
        except (OSError, ValueError, KeyError, TypeError) as error:
            raise AdapterParseError("không đọc được QC_EVAL_OUTPUTS") from error
        if not isinstance(raw_records, list) or not raw_records:
            raise AdapterParseError("QC_EVAL_OUTPUTS không có record")
        case_ids = [record.get("id") if isinstance(record, dict) else None for record in raw_records]
        if (
            any(not isinstance(case_id, str) or not re.fullmatch(r"[a-z0-9]+", case_id) for case_id in case_ids)
            or len(set(case_ids)) != len(case_ids)
        ):
            raise AdapterParseError("QC_EVAL_OUTPUTS có id thiếu, sai định dạng hoặc trùng")
        expected_pairs = {(metric, case_id) for metric in METRICS for case_id in case_ids}

        testcases = [element for element in root.iter() if _tag(element) == "testcase"]
        if not testcases:
            raise AdapterParseError("JUnit không có testcase")
        parsed_cases: dict[tuple[str, str], bool] = {}
        failures: list[tuple[str, str]] = []
        geval_seen = False
        for testcase in testcases:
            name = testcase.attrib.get("name", "")
            children = {_tag(child) for child in testcase}
            if "error" in children or "skipped" in children:
                raise AdapterParseError("JUnit có error/skipped; không thể chứng minh đủ các metric")
            if name == "test_geval_advisory":
                if geval_seen or "failure" in children:
                    raise AdapterParseError("JUnit G-Eval testcase bị trùng hoặc thất bại")
                geval_seen = True
                continue
            match = METRIC_CASE_RE.fullmatch(name)
            if match is None:
                raise AdapterParseError(f"JUnit testcase không khớp metric contract: {name[:160]!r}")
            metric, case_id = match.group("metric"), match.group("case")
            pair = (metric, case_id)
            if metric not in METRICS or pair not in expected_pairs or pair in parsed_cases:
                raise AdapterParseError(f"JUnit có metric/case lạ hoặc trùng: {name!r}")
            failed = "failure" in children
            parsed_cases[pair] = not failed
            if failed:
                failures.append(pair)

        if not geval_seen:
            raise AdapterParseError("JUnit thiếu test_geval_advisory")
        if set(parsed_cases) != expected_pairs:
            missing = sorted(expected_pairs - set(parsed_cases))
            raise AdapterParseError(f"JUnit thiếu cặp metric/case: {missing[:4]}")
        if proc.returncode not in (0, 1):
            raise AdapterParseError(f"pytest exit code infrastructure error: {proc.returncode}")
        if (proc.returncode == 1) != bool(failures):
            raise AdapterParseError("mâu thuẫn exit code/báo cáo JUnit")

        checks = {metric: True for metric in METRICS}
        for metric, case_id in failures:
            checks[metric] = False
        findings = [
            {
                "finding_id": f"f-de-{metric}-{case_id}",
                "title": f"{metric}: case {case_id} không đạt",
                "detected_by": f"metric:{metric}",
                "verdict_source": "deterministic_assert",
                "severity_hint": "high",
            }
            for metric, case_id in failures
        ]

        metrics: dict[str, float | str] = {}
        evidence_paths: list[tuple[str, Path]] = [("raw_output", report_path)]
        adapter_notes = [f"PARSER_VERSION={PARSER_VERSION}", "confidence := G-Eval score"]
        geval_path = workdir / GEVAL_NAME
        if geval_path.is_file():
            evidence_paths.append(("raw_output", geval_path))
            try:
                geval = json.loads(geval_path.read_text(encoding="utf-8-sig"))
                if not isinstance(geval, dict):
                    raise ValueError("report must be an object")
                score = geval.get("score")
                case_scores = geval.get("cases")
                provider = geval.get("judge_provider")
                model = geval.get("judge_model")
                if (
                    isinstance(score, bool)
                    or not isinstance(score, (int, float))
                    or not math.isfinite(float(score))
                    or not 0.0 <= float(score) <= 1.0
                    or not isinstance(provider, str)
                    or not provider
                    or not isinstance(model, str)
                    or not model
                    or not isinstance(case_scores, dict)
                    or set(case_scores) != set(case_ids)
                ):
                    raise ValueError("missing advisory fields")
                score = float(score)
                parsed_scores: list[float] = []
                for case_score in case_scores.values():
                    if (
                        isinstance(case_score, bool)
                        or not isinstance(case_score, (int, float))
                        or not math.isfinite(float(case_score))
                        or not 0.0 <= float(case_score) <= 1.0
                    ):
                        raise ValueError("case score must be finite and in 0..1")
                    parsed_scores.append(float(case_score))
                mean_score = sum(parsed_scores) / len(parsed_scores)
                if not math.isclose(score, mean_score, rel_tol=0.0, abs_tol=1e-6):
                    raise ValueError("overall score does not match case score mean")
                metrics["GEval.score"] = score
                findings.append(
                    {
                        "finding_id": "f-de-geval-summary",
                        "title": "G-Eval đánh giá khả năng giữ ý chính",
                        "detected_by": "metric:GEval",
                        "verdict_source": "llm_judgment",
                        "confidence": score,
                        "rationale": f"G-Eval 'giữ ý chính' {score:.2f} (judge={provider}/{model})",
                    }
                )
                adapter_notes.append(f"G-Eval judge={provider}/{model}")
            except (OSError, ValueError, TypeError, AttributeError):
                adapter_notes.append("G-Eval advisory report không hợp lệ; deterministic checks không đổi")
        else:
            adapter_notes.append("không có G-Eval report; deterministic checks không đổi")

        evidence_paths.append(("stdout", stdout_path))
        args = proc.args if isinstance(proc.args, (list, tuple)) else [str(proc.args)]
        return ParsedOutput(
            metrics=metrics,
            findings=findings,
            signals={"checks": checks},
            evidence_paths=evidence_paths,
            tokens=None,
            usd=None,
            exit_code=proc.returncode,
            replay_cmd=shlex.join([str(argument) for argument in args]),
            adapter_notes=adapter_notes,
        )


if __name__ == "__main__":
    raise SystemExit(DeepEvalAdapter().main())
