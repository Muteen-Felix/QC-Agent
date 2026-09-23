"""Stub summarizer TẤT ĐỊNH — SUT GIẢ LẬP của AI feature (quyết định D-01). KHÔNG gọi model.
BUG-3 (cài sẵn): body chứa marker [[long]] thì summary DÀI HƠN body.
"""
import hashlib

MODEL = "stub-rule-v1"
PROMPT = "summarize:first-sentence-v1"
PROMPT_HASH = hashlib.sha256(PROMPT.encode("utf-8")).hexdigest()[:12]


def summarize(body: str, bug3: bool = True) -> str:
    body = body.strip()
    if bug3 and "[[long]]" in body:
        return (body + " ") * 2 + "(tóm tắt mở rộng)"
    first = body.split(".")[0].strip() or body
    return first[: max(1, len(body) - 1)]
