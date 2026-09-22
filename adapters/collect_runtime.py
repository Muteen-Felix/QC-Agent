"""HTTP collection implementation executed inside the budgeted worker process."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Callable
from urllib.parse import urlsplit

import httpx


OUTPUT_NAME = "outputs.json"
REPORT_NAME = "collection.json"


class CollectionError(Exception):
    """Collection could not produce a trustworthy result."""


def collect(
    golden_path: Path,
    base_url: str,
    client_factory: Callable[..., httpx.Client] = httpx.Client,
) -> tuple[list[dict], dict]:
    if not golden_path.is_file():
        raise CollectionError("golden file does not exist")
    parsed_url = urlsplit(base_url)
    if parsed_url.scheme not in {"http", "https"} or not parsed_url.netloc:
        raise CollectionError("base URL must be an absolute HTTP(S) URL")
    try:
        cases = json.loads(golden_path.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError) as error:
        raise CollectionError(f"cannot read golden file: {type(error).__name__}") from error
    if not isinstance(cases, list) or not cases:
        raise CollectionError("golden file must be a non-empty list")
    for index, case in enumerate(cases):
        if (
            not isinstance(case, dict)
            or not isinstance(case.get("id"), str)
            or not case["id"].strip()
            or not isinstance(case.get("title"), str)
            or not case["title"].strip()
            or not isinstance(case.get("body"), str)
            or not case["body"].strip()
        ):
            raise CollectionError(f"golden case #{index + 1} has invalid id/title/body")
    if len({case["id"] for case in cases}) != len(cases):
        raise CollectionError("golden case ids must be unique")

    records: list[dict] = []
    created_ids: list[int] = []
    all_http_2xx = True
    transport_error: httpx.RequestError | None = None
    try:
        with client_factory(base_url=base_url.rstrip("/"), timeout=10.0) as client:
            try:
                for case in cases:
                    record = {
                        "id": case["id"],
                        "note_id": None,
                        "input": case["body"],
                        "actual_output": "",
                        "model": "",
                        "prompt_hash": "",
                        "http_status": None,
                    }
                    create_response = client.post(
                        "/notes", json={"title": case["title"], "body": case["body"]}
                    )
                    record["http_status"] = create_response.status_code
                    all_http_2xx = create_response.is_success and all_http_2xx
                    if create_response.is_success:
                        try:
                            created = create_response.json()
                        except ValueError as error:
                            raise CollectionError("POST /notes returned invalid JSON") from error
                        note_id = created.get("id") if isinstance(created, dict) else None
                        if isinstance(note_id, bool) or not isinstance(note_id, int) or note_id < 1:
                            raise CollectionError("POST /notes response has no positive integer id")
                        created_ids.append(note_id)
                        record["note_id"] = note_id

                        summary_response = client.post(f"/notes/{note_id}/summarize")
                        record["http_status"] = summary_response.status_code
                        all_http_2xx = summary_response.is_success and all_http_2xx
                        if summary_response.is_success:
                            try:
                                summary = summary_response.json()
                            except ValueError as error:
                                raise CollectionError(
                                    "POST /notes/{id}/summarize returned invalid JSON"
                                ) from error
                            if (
                                not isinstance(summary, dict)
                                or not isinstance(summary.get("summary"), str)
                                or not isinstance(summary.get("model"), str)
                                or not isinstance(summary.get("prompt_hash"), str)
                            ):
                                raise CollectionError(
                                    "summary response misses summary/model/prompt_hash strings"
                                )
                            record["actual_output"] = summary["summary"]
                            record["model"] = summary["model"]
                            record["prompt_hash"] = summary["prompt_hash"]
                    records.append(record)
            except httpx.RequestError as error:
                transport_error = error
            finally:
                # Attempt every cleanup even if one DELETE fails.
                for note_id in created_ids:
                    try:
                        delete_response = client.delete(f"/notes/{note_id}")
                        all_http_2xx = delete_response.is_success and all_http_2xx
                    except httpx.RequestError as error:
                        all_http_2xx = False
                        if transport_error is None:
                            transport_error = error
    except httpx.RequestError as error:
        transport_error = transport_error or error

    if transport_error is not None:
        raise CollectionError(f"HTTP transport error: {type(transport_error).__name__}") from transport_error

    return records, {
        "count": len(records),
        "expected_count": len(cases),
        "checks": {
            "all_http_2xx": bool(all_http_2xx),
            "count_matches_golden": len(records) == len(cases),
        },
    }


def write_collection_artifacts(
    records: list[dict], report: dict, output_path: Path, report_path: Path
) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(records, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    report_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
