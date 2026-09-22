"""Deterministic checks for collected summarizer output plus advisory G-Eval."""
from __future__ import annotations

import json
import os
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator

from adapters.deepeval_runtime import run_geval_advisory


pytestmark = pytest.mark.skipif(
    not os.environ.get("QC_EVAL_OUTPUTS"),
    reason="only run through deepeval_adapter",
)

METRICS = (
    "summary_not_empty",
    "summary_shorter_than_body",
    "summary_json_valid",
)
RECORD_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["id", "note_id", "input", "actual_output", "model", "prompt_hash", "http_status"],
    "properties": {
        "id": {"type": "string", "pattern": "^[a-z0-9]+$"},
        "note_id": {"type": ["integer", "null"], "minimum": 1},
        "input": {"type": "string", "minLength": 1},
        "actual_output": {"type": "string"},
        "model": {"type": "string"},
        "prompt_hash": {"type": "string"},
        "http_status": {"type": ["integer", "null"], "minimum": 100, "maximum": 599},
    },
}


def _load_records() -> list[dict]:
    path_text = os.environ.get("QC_EVAL_OUTPUTS", "")
    if not path_text:
        return []
    records = json.loads(Path(path_text).read_text(encoding="utf-8-sig"))
    if not isinstance(records, list) or not records:
        raise ValueError("QC_EVAL_OUTPUTS must contain a non-empty JSON list")
    ids = [record.get("id") for record in records if isinstance(record, dict)]
    if len(ids) != len(records) or any(not isinstance(case_id, str) for case_id in ids):
        raise ValueError("each output record must have a string id")
    if len(set(ids)) != len(ids):
        raise ValueError("output record ids must be unique")
    return records


def _metric_params():
    if not os.environ.get("QC_EVAL_OUTPUTS"):
        return [pytest.param("summary_not_empty", {}, id="outputs-unset")]
    return [
        pytest.param(metric, record, id=f"{metric}-{record['id']}")
        for metric in METRICS
        for record in _load_records()
    ]


def _metric_passes(metric_name: str, record: dict) -> bool:
    if metric_name == "summary_not_empty":
        output = record.get("actual_output")
        return isinstance(output, str) and bool(output.strip())
    if metric_name == "summary_shorter_than_body":
        body, output = record.get("input"), record.get("actual_output")
        return isinstance(body, str) and isinstance(output, str) and len(output) < len(body)
    if metric_name == "summary_json_valid":
        return not list(Draft202012Validator(RECORD_SCHEMA).iter_errors(record))
    raise ValueError(f"unsupported metric: {metric_name}")


@pytest.mark.parametrize(("metric_name", "record"), _metric_params())
def test_metric(metric_name: str, record: dict) -> None:
    assert _metric_passes(metric_name, record), f"{metric_name} failed for {record.get('id', '?')}"


def test_geval_advisory() -> None:
    records = _load_records()
    output_dir = Path(os.environ.get("QC_EVAL_OUT_DIR", "."))
    primary = {
        "provider": os.environ.get("QC_JUDGE_PROVIDER", ""),
        "model": os.environ.get("QC_JUDGE_MODEL", ""),
    }
    fallback = {
        "provider": os.environ.get("QC_JUDGE_FALLBACK_PROVIDER", ""),
        "model": os.environ.get("QC_JUDGE_FALLBACK_MODEL", ""),
    }
    run_geval_advisory(records, output_dir, primary, fallback)
