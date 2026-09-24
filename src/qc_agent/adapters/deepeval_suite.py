"""Bộ pytest CỦA qc-agent (không nằm trong repo SUT nên không chạy mã của PR): metric tất định + G-Eval advisory.

Cấu hình đến từ file JSON ở QC_EVAL_CONFIG do adapter ghi (metrics, geval), dữ liệu từ QC_EVAL_OUTPUTS. Chỉ chạy qua deepeval_worker."""
from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from qc_agent.adapters import deepeval_metrics
from qc_agent.adapters.deepeval_runtime import run_geval_advisory

pytestmark = pytest.mark.skipif(not os.environ.get("QC_EVAL_OUTPUTS") or not os.environ.get("QC_EVAL_CONFIG"),
                                reason="only run through deepeval_worker")


def _config() -> dict:
    return json.loads(Path(os.environ["QC_EVAL_CONFIG"]).read_text(encoding="utf-8"))


def _load_records() -> list[dict]:
    records = json.loads(Path(os.environ["QC_EVAL_OUTPUTS"]).read_text(encoding="utf-8-sig"))
    if not isinstance(records, list) or not records:
        raise ValueError("QC_EVAL_OUTPUTS must contain a non-empty JSON list")
    ids = [record.get("id") for record in records if isinstance(record, dict)]
    if len(ids) != len(records) or any(not isinstance(case_id, str) for case_id in ids):
        raise ValueError("each output record must have a string id")
    if len(set(ids)) != len(ids):
        raise ValueError("output record ids must be unique")
    return records


def _metric_params():
    if not os.environ.get("QC_EVAL_OUTPUTS") or not os.environ.get("QC_EVAL_CONFIG"):
        return [pytest.param({}, {}, id="outputs-unset")]
    records = _load_records()
    return [pytest.param(metric, record, id=f"{metric['name']}-{record['id']}") for metric in _config()["metrics"] for record in records]


@pytest.mark.parametrize(("metric", "record"), _metric_params())
def test_metric(metric: dict, record: dict) -> None:
    assert deepeval_metrics.passes(metric, record), f"{metric['name']} failed for {record.get('id', '?')}"


def test_geval_advisory() -> None:
    geval = _config()["geval"]  # worker bỏ chọn test này khi suite không cấu hình geval
    output_dir = Path(os.environ.get("QC_EVAL_OUT_DIR", "."))
    primary = {"provider": os.environ.get("QC_JUDGE_PROVIDER", ""), "model": os.environ.get("QC_JUDGE_MODEL", "")}
    fallback = {"provider": os.environ.get("QC_JUDGE_FALLBACK_PROVIDER", ""), "model": os.environ.get("QC_JUDGE_FALLBACK_MODEL", "")}
    run_geval_advisory(_load_records(), output_dir, primary, fallback, geval)
