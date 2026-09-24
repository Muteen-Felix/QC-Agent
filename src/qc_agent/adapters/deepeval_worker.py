"""Tiến trình worker của deepeval: (tuỳ chọn) thu thập output của SUT qua HTTP, rồi chạy bộ pytest của qc-agent.

Chạy trong tiến trình con có ngân sách của adapter, nên thu thập bị giới hạn bởi budget.wallclock_s như mọi thứ khác.
Exit: kết quả của pytest (0/1 = metric đạt/không đạt; khác = hạ tầng) hoặc 3 = thu thập không đáng tin (adapter báo `error`)."""
from __future__ import annotations

import argparse
import importlib.util
import json
import os
import sys
from pathlib import Path

import pytest

from qc_agent.adapters import deepeval_collect


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", required=True)
    args = ap.parse_args(argv)
    config = json.loads(Path(args.config).read_text(encoding="utf-8"))
    workdir = Path(config["workdir"])
    outputs = workdir / deepeval_collect.OUTPUT_NAME
    if config.get("collect"):
        try:
            records, report = deepeval_collect.collect(config["collect"], os.environ["QC_COLLECT_BASE_URL"], Path(config["golden_path"]))
        except deepeval_collect.CollectionError as error:
            sys.stderr.write(f"collect: {error}\n")
            return deepeval_collect.EXIT_COLLECT_FAILED
        deepeval_collect.write_collection_artifacts(records, report, outputs, workdir / deepeval_collect.REPORT_NAME)
    else:
        outputs = Path(config["outputs_path"])
    os.environ["QC_EVAL_OUTPUTS"] = str(outputs)
    os.environ["QC_EVAL_CONFIG"] = str(args.config)
    ini = workdir / "pytest.ini"  # ini rỗng: không để pytest.ini/conftest của SUT chen vào bộ test của qc-agent
    ini.write_text("[pytest]\n", encoding="utf-8")
    # KHÔNG import deepeval_suite ở đây: module đọc biến môi trường lúc import, phải để pytest import sau khi đã đặt chúng
    suite = Path(importlib.util.find_spec("qc_agent.adapters.deepeval_suite").origin).resolve()
    pytest_args = [str(suite), f"--junitxml={workdir / 'junit.xml'}", "-q", "-p", "no:cacheprovider", "-c", str(ini),
                   "--rootdir", str(workdir), "--import-mode=importlib"]
    if not config.get("geval"):
        pytest_args.append(f"--deselect={suite}::test_geval_advisory")
    return int(pytest.main(pytest_args))


if __name__ == "__main__":
    raise SystemExit(main())
