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
from urllib.parse import urlsplit

from qc_agent.adapters import deepeval_collect, deepeval_metrics
from qc_agent.adapters._base import Adapter, AdapterParseError, ParsedOutput


PARSER_VERSION = "3"
JUNIT_NAME = "junit.xml"
GEVAL_NAME = "geval.json"
STDOUT_NAME = "stdout.log"
CONFIG_NAME = "eval_config.json"
COLLECT_CHECKS = ("all_http_2xx", "count_matches_golden")
GEVAL_PARAMS = {"input", "actual_output", "expected_output", "context"}
METRIC_CASE_RE = re.compile(r"^test_metric\[(?P<metric>[a-z][a-z0-9_]*)-(?P<case>[a-z0-9]+)\]$")


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
        problems = deepeval_metrics.validate(inputs.get("metrics"))
        geval = inputs.get("geval")
        if geval is not None:
            problems += self._validate_geval(geval)
            self._check_judge(inputs)
        collect = inputs.get("collect")
        if collect is not None:
            problems += deepeval_collect.validate_config(collect)
        if problems:
            raise AdapterParseError(problems[0])

        config = {"metrics": inputs["metrics"], "geval": geval, "workdir": str(workdir.resolve())}
        if collect is not None:
            golden = Path(collect["golden"]).resolve()
            if not golden.is_file():
                raise AdapterParseError(f"golden file không tồn tại: {golden}")
            base_url = spec.get("target", {}).get("base_url")
            parsed = urlsplit(base_url) if isinstance(base_url, str) else None
            if not parsed or parsed.scheme not in {"http", "https"} or not parsed.netloc:
                raise AdapterParseError("target.base_url phải là URL HTTP(S) tuyệt đối")
            if parsed.username or parsed.password or parsed.query or parsed.fragment:
                raise AdapterParseError("target.base_url không được chứa thông tin đăng nhập, query hoặc fragment")
            # base_url đi qua môi trường, không qua argv: argv được lưu trong determinism.replay_cmd.
            self.env["QC_COLLECT_BASE_URL"] = base_url
            config.update(collect=collect, golden_path=str(golden))
        else:
            outputs_path = inputs.get("outputs_path")
            if not isinstance(outputs_path, str) or not outputs_path.strip():
                raise AdapterParseError("cần inputs.collect hoặc inputs.outputs_path")
            if not Path(outputs_path).resolve().is_file():
                raise AdapterParseError("inputs.outputs_path không tồn tại")
            config["outputs_path"] = str(Path(outputs_path).resolve())

        report_path = (workdir / JUNIT_NAME).resolve()
        report_path.unlink(missing_ok=True)
        (workdir / GEVAL_NAME).unlink(missing_ok=True)
        config_path = (workdir / CONFIG_NAME).resolve()
        config_path.write_text(json.dumps(config, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        out_dir = workdir.resolve()
        self.env.update({
            "QC_EVAL_OUT_DIR": str(out_dir),
            "DEEPEVAL_RESULTS_FOLDER": str(out_dir / "deepeval-results"),
            # Giữ ranh giới data-egress đã khai báo: chỉ tới nhà cung cấp judge.
            "DEEPEVAL_TELEMETRY_OPT_OUT": "YES",
        })
        judge_config = inputs.get("judge_config") or {}
        for field, env_name in (("provider", "QC_JUDGE_PROVIDER"), ("model", "QC_JUDGE_MODEL"),
                                ("fallback_provider", "QC_JUDGE_FALLBACK_PROVIDER"), ("fallback_model", "QC_JUDGE_FALLBACK_MODEL")):
            value = judge_config.get(field)
            if value is not None:
                if not isinstance(value, str):
                    raise AdapterParseError(f"judge_config.{field} phải là chuỗi")
                self.env[env_name] = value
        python = os.environ.get("QC_DEEPEVAL_PYTHON") or sys.executable
        return [python, "-m", "qc_agent.adapters.deepeval_worker", "--config", str(config_path)]

    @staticmethod
    def _validate_geval(geval) -> list[str]:
        if (not isinstance(geval, dict) or set(geval) - {"name", "criteria", "params", "baseline"}
                or not isinstance(geval.get("name"), str) or not geval["name"].strip()
                or not isinstance(geval.get("criteria"), str) or not geval["criteria"].strip()):
            return ["inputs.geval phải là {name, criteria, params?, baseline?} (name/criteria là chuỗi không rỗng)"]
        params = geval.get("params", ["input", "actual_output"])
        baseline = geval.get("baseline")
        if not isinstance(params, list) or not params or any(p not in GEVAL_PARAMS for p in params):
            return [f"inputs.geval.params phải là danh sách con của {sorted(GEVAL_PARAMS)}"]
        if baseline is not None and (isinstance(baseline, bool) or not isinstance(baseline, (int, float)) or not 0 <= baseline <= 1):
            return ["inputs.geval.baseline phải là số trong 0..1"]
        return []

    @staticmethod
    def _check_judge(inputs: dict) -> None:
        judge_config = inputs.get("judge_config")
        if not isinstance(judge_config, dict):
            raise AdapterParseError("inputs.judge_config phải là object")
        primary_model = judge_config.get("model")
        sut_model = inputs.get("sut_model")
        if not isinstance(primary_model, str) or not primary_model.strip():
            raise AdapterParseError("inputs.judge_config.model phải là tên model")
        if not isinstance(sut_model, str) or not sut_model.strip():
            raise AdapterParseError("inputs.sut_model phải là tên model")
        configured = [primary_model]
        fallback_model = judge_config.get("fallback_model")
        if isinstance(fallback_model, str) and fallback_model.strip():
            configured.append(fallback_model)
        if any(model.strip() == sut_model.strip() for model in configured):
            raise AdapterParseError("judge trùng model của SUT")

    @staticmethod
    def _collection_checks(workdir: Path, records: list) -> dict[str, bool]:
        try:
            report = json.loads((workdir / deepeval_collect.REPORT_NAME).read_text(encoding="utf-8-sig"))
        except (OSError, ValueError) as error:
            raise AdapterParseError("collection.json không đọc được") from error
        checks = report.get("checks") if isinstance(report, dict) else None
        if (not isinstance(report, dict) or set(report) != {"count", "expected_count", "checks"}
                or any(isinstance(report[k], bool) or not isinstance(report[k], int) for k in ("count", "expected_count"))
                or not isinstance(checks, dict) or set(checks) != set(COLLECT_CHECKS)
                or any(not isinstance(v, bool) for v in checks.values())):
            raise AdapterParseError("collection.json sai schema")
        if (report["count"] != len(records) or report["expected_count"] < 1
                or checks["count_matches_golden"] != (report["count"] == report["expected_count"])):
            raise AdapterParseError("collection.json mâu thuẫn với outputs.json")
        return dict(checks)

    def parse_output(
        self, proc: subprocess.CompletedProcess, workdir: Path, spec: dict
    ) -> ParsedOutput:
        stdout_path = workdir / STDOUT_NAME
        stdout_path.write_text(proc.stdout or "", encoding="utf-8")
        if proc.returncode == deepeval_collect.EXIT_COLLECT_FAILED:
            reason = (proc.stderr or "").strip().splitlines()[-1:] or ["không rõ"]
            raise AdapterParseError(f"thu thập output của SUT thất bại: {reason[0][:200]}")
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

        inputs = spec["inputs"]
        metric_names = [metric["name"] for metric in inputs["metrics"]]
        has_geval = inputs.get("geval") is not None
        outputs_path = workdir / deepeval_collect.OUTPUT_NAME if inputs.get("collect") is not None else Path(inputs["outputs_path"])
        try:
            raw_records = json.loads(outputs_path.read_text(encoding="utf-8-sig"))
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
        expected_pairs = {(metric, case_id) for metric in metric_names for case_id in case_ids}

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
                if not has_geval or geval_seen or "failure" in children:
                    raise AdapterParseError("JUnit G-Eval testcase bị trùng hoặc thất bại")
                geval_seen = True
                continue
            match = METRIC_CASE_RE.fullmatch(name)
            if match is None:
                raise AdapterParseError(f"JUnit testcase không khớp metric contract: {name[:160]!r}")
            metric, case_id = match.group("metric"), match.group("case")
            pair = (metric, case_id)
            if metric not in metric_names or pair not in expected_pairs or pair in parsed_cases:
                raise AdapterParseError(f"JUnit có metric/case lạ hoặc trùng: {name!r}")
            failed = "failure" in children
            parsed_cases[pair] = not failed
            if failed:
                failures.append(pair)

        if has_geval and not geval_seen:
            raise AdapterParseError("JUnit thiếu test_geval_advisory")
        if set(parsed_cases) != expected_pairs:
            missing = sorted(expected_pairs - set(parsed_cases))
            raise AdapterParseError(f"JUnit thiếu cặp metric/case: {missing[:4]}")
        if proc.returncode not in (0, 1):
            raise AdapterParseError(f"pytest exit code infrastructure error: {proc.returncode}")
        if (proc.returncode == 1) != bool(failures):
            raise AdapterParseError("mâu thuẫn exit code/báo cáo JUnit")

        checks = {metric: True for metric in metric_names}
        collect_evidence: list[tuple[str, Path]] = []
        if inputs.get("collect") is not None:
            checks.update(self._collection_checks(workdir, raw_records))
            collect_evidence = [("raw_output", outputs_path), ("raw_output", workdir / deepeval_collect.REPORT_NAME)]
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
        evidence_paths: list[tuple[str, Path]] = [("raw_output", report_path), *collect_evidence]
        adapter_notes = [f"PARSER_VERSION={PARSER_VERSION}", "confidence := G-Eval score"]
        geval_path = workdir / GEVAL_NAME
        if not has_geval:
            pass
        elif geval_path.is_file():
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
                if inputs["geval"].get("baseline") is not None:
                    metrics["GEval.baseline"] = float(inputs["geval"]["baseline"])  # so sánh nằm ở báo cáo (không chặn gate)
                findings.append(
                    {
                        "finding_id": "f-de-geval-summary",
                        "title": f"G-Eval '{inputs['geval']['name']}'",
                        "detected_by": "metric:GEval",
                        "verdict_source": "llm_judgment",
                        "confidence": score,
                        "rationale": f"G-Eval '{inputs['geval']['name']}' {score:.2f} (judge={provider}/{model})",
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
