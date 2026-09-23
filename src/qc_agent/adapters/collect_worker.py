"""Budgeted subprocess entry point for STEP 36 HTTP collection."""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from qc_agent.adapters.collect_runtime import CollectionError, collect, write_collection_artifacts


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Collect summarizer outputs over HTTP")
    parser.add_argument("--golden", required=True)
    parser.add_argument("--out", required=True)
    parser.add_argument("--report", required=True)
    args = parser.parse_args(argv)
    try:
        base_url = os.environ.get("QC_COLLECT_BASE_URL", "")
        records, report = collect(Path(args.golden), base_url)
        write_collection_artifacts(records, report, Path(args.out), Path(args.report))
    except (CollectionError, OSError) as error:
        # Collection errors are sanitized and never include response bodies or credentials.
        print(f"collection failed: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
