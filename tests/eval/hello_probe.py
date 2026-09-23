"""STEP 35 smoke probe for DeepEval's pytest and JUnit behavior.

This file intentionally does not match pytest's default test discovery patterns.
Run it explicitly: ``pytest tests/eval/hello_probe.py``. The ``empty`` case is
deliberately failing so the JUnit report demonstrates a deterministic failure.
"""
from __future__ import annotations

import os

import pytest
from deepeval import assert_test
from deepeval.metrics import BaseMetric, GEval
from deepeval.test_case import LLMTestCase, SingleTurnParams


class NotEmpty(BaseMetric):
    """A key-free DeepEval metric used to verify pytest integration."""

    def __init__(self, threshold: float = 1.0):
        self.threshold = threshold
        self.score = None
        self.success = None
        self.reason = None
        self.error = None
        self.evaluation_cost = None
        self.verbose_logs = None

    @property
    def __name__(self):
        return "summary_not_empty"

    def measure(self, test_case: LLMTestCase, *args, **kwargs) -> float:
        output = test_case.actual_output
        self.score = 1.0 if isinstance(output, str) and output.strip() else 0.0
        self.success = self.score >= self.threshold
        self.reason = "non-empty output" if self.success else "empty output"
        return self.score

    async def a_measure(self, test_case: LLMTestCase, *args, **kwargs) -> float:
        return self.measure(test_case)

    def is_successful(self) -> bool:
        return bool(self.success)


@pytest.mark.parametrize(
    "case_id,actual_output",
    [("ok", "Tóm tắt ngắn"), ("empty", "")],
    ids=["ok", "empty"],
)
def test_not_empty(case_id: str, actual_output: str) -> None:
    del case_id  # the id is retained in pytest/JUnit's case name
    assert_test(
        test_case=LLMTestCase(input="Ghi chú mẫu", actual_output=actual_output),
        metrics=[NotEmpty()],
        run_async=False,
    )


def test_geval_score() -> None:
    provider = os.environ.get("QC_JUDGE_PROVIDER", "").strip().lower()
    model_name = os.environ.get("QC_JUDGE_MODEL", "").strip()
    if not model_name:
        pytest.skip("QC_JUDGE_MODEL is not set")

    if provider == "gemini":
        from deepeval.models import GeminiModel

        api_key = os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")
        if not api_key:
            pytest.skip("Gemini API key is not set")
        model = GeminiModel(model=model_name, api_key=api_key, temperature=0)
    elif provider == "openai":
        if not os.environ.get("OPENAI_API_KEY"):
            pytest.skip("OpenAI API key is not set")
        model = model_name
    else:
        pytest.skip("QC_JUDGE_PROVIDER must be openai or gemini")

    metric = GEval(
        name="giu_y_chinh",
        criteria="Bản tóm tắt giữ lại các ý chính trong đầu vào.",
        evaluation_params=[SingleTurnParams.INPUT, SingleTurnParams.ACTUAL_OUTPUT],
        model=model,
        async_mode=False,
    )
    metric.measure(
        LLMTestCase(input="Họp lúc 9h. Mang laptop.", actual_output="Họp 9h, mang laptop.")
    )
    print(f"GEVAL_SCORE {metric.score}")
