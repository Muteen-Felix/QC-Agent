"""GENERATED bởi tools/auto_promote.py — REVIEW TRƯỚC KHI MERGE.

run_id: r-0032
finding_id: f-sig-dom_unchanged
task_id: t-101
detected_by: implicit_signal:dom_unchanged
evidence_sha256: dd3298fcb67d3e3a307ca32f576e36e521dd95e43f0777670c95afa925982237
generated_at: 2026-09-22T18:10:17

Nguồn: promote_candidate trong runs/r-0032/results/t-101.json.
Bước không map được sẽ pytest.fail() tường minh thay vì âm thầm pass.

Chạy:  pytest tests_generated/test_promoted_f_sig_dom_unchanged.py -q
SKIP cho tới khi cài: pip install playwright && python -m playwright install chromium
(chưa cài trong .venv của repo này — dùng promoted_f_sig_dom_unchanged.spec.mjs để chạy thật bằng Node).
"""
from __future__ import annotations

import os
import uuid

import pytest

pytest.importorskip("playwright.sync_api", reason="playwright (python) chưa cài trong .venv này")
from playwright.sync_api import sync_playwright  # noqa: E402

BASE = os.environ.get("APP_BASE_URL", "http://127.0.0.1:8000")


def test_promoted_f_sig_dom_unchanged():
    title = f"qc-{uuid.uuid4().hex[:8]}"
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page()
        page.goto(BASE + "/")
        page.fill("#title", title)
        page.fill("#body", "auto-promote")
        page.click('button[type=submit]')
        page.wait_for_selector(f"#notes li:has-text('{title}')")
        page.click(f"#notes li:has-text('{title}') >> text=Xoá")
        page.wait_for_function(
            "t => ![...document.querySelectorAll('#notes li')].some(li => li.textContent.includes(t))",
            arg=title, timeout=3000)
        browser.close()
