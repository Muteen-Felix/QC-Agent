"""Ground-Truth Engine (vòng ngoài, S1): PRD -> test case dạng dữ liệu -> file qua PR, QA duyệt.

Cố ý không import gì ở đây: `core/` không được kéo `qc_agent.groundtruth` vào tiến trình (luật import lười, docs/core-rules.md).
LLM chỉ sinh DỮ LIỆU (JSON test case); code test được render tất định từ template, nên QA duyệt dữ liệu chứ không duyệt code.
"""
