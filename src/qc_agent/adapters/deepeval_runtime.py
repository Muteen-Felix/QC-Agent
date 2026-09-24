"""DeepEval advisory judge helpers shared by the pytest task and adapter tests."""
from __future__ import annotations

import json
import math
import os
from pathlib import Path

from deepeval.metrics import GEval
from deepeval.test_case import LLMTestCase, SingleTurnParams


DEFAULT_GEVAL = {"name": "giu_y_chinh", "criteria": "Bản tóm tắt giữ lại các ý chính trong đầu vào.", "params": ["input", "actual_output"]}
_PARAMS = {"input": SingleTurnParams.INPUT, "actual_output": SingleTurnParams.ACTUAL_OUTPUT,
           "expected_output": SingleTurnParams.EXPECTED_OUTPUT, "context": SingleTurnParams.CONTEXT}


def _score_cases(records: list[dict], provider: str, model_name: str, geval: dict) -> dict[str, float]:
    provider = provider.strip().lower()
    if provider == "openai":
        from deepeval.models import OpenAIModel

        api_key = os.environ.get("OPENAI_API_KEY")
        if not api_key:
            raise RuntimeError("judge credentials are unavailable")
        model = OpenAIModel(model=model_name, api_key=api_key, temperature=0)
    elif provider == "gemini":
        from deepeval.models import GeminiModel

        api_key = os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")
        if not api_key:
            raise RuntimeError("judge credentials are unavailable")
        model = GeminiModel(model=model_name, api_key=api_key, temperature=0)
    else:
        raise RuntimeError("unsupported judge provider")

    scores: dict[str, float] = {}
    for record in records:
        metric = GEval(
            name=geval["name"],
            criteria=geval["criteria"],
            evaluation_params=[_PARAMS[name] for name in geval["params"]],
            model=model,
            async_mode=False,
        )
        metric.measure(LLMTestCase(input=record["input"], actual_output=record["actual_output"],
                                   **{name: record[name] for name in geval["params"] if name not in ("input", "actual_output")}))
        score = metric.score
        if isinstance(score, bool) or not isinstance(score, (int, float)):
            raise RuntimeError("judge returned no numeric score")
        score = float(score)
        if not math.isfinite(score) or not 0.0 <= score <= 1.0:
            raise RuntimeError("judge returned a score outside 0..1")
        scores[record["id"]] = score
    return scores


def run_geval_advisory(
    records: list[dict],
    output_dir: Path,
    primary: dict[str, str],
    fallback: dict[str, str] | None,
    geval: dict | None = None,
) -> dict:
    """Try the configured primary and at most one fallback; never expose provider errors."""
    candidates = [primary, fallback or {}]
    attempted: set[tuple[str, str]] = set()
    error_type = "RuntimeError"

    for candidate in candidates:
        provider = candidate.get("provider", "").strip().lower()
        model_name = candidate.get("model", "").strip()
        identity = (provider, model_name)
        if not provider or not model_name or identity in attempted:
            continue
        attempted.add(identity)
        try:
            case_scores = _score_cases(records, provider, model_name, {**DEFAULT_GEVAL, **(geval or {})})
            if set(case_scores) != {record["id"] for record in records}:
                raise RuntimeError("judge response is incomplete")
            for score in case_scores.values():
                if (
                    isinstance(score, bool)
                    or not isinstance(score, (int, float))
                    or not math.isfinite(float(score))
                    or not 0.0 <= float(score) <= 1.0
                ):
                    raise RuntimeError("judge returned an invalid score")
            result = {
                "score": sum(float(score) for score in case_scores.values()) / len(case_scores),
                "cases": case_scores,
                "judge_provider": provider,
                "judge_model": model_name,
            }
            _write_geval(output_dir, result)
            return result
        except Exception as error:  # advisory must not fail deterministic pytest checks
            error_type = type(error).__name__

    result = {"error": "judge evaluation failed", "error_type": error_type}
    _write_geval(output_dir, result)
    return result


def _write_geval(output_dir: Path, result: dict) -> None:
    try:
        output_dir.mkdir(parents=True, exist_ok=True)
        (output_dir / "geval.json").write_text(
            json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
    except OSError:
        # G-Eval remains advisory even when its report cannot be saved.
        return
