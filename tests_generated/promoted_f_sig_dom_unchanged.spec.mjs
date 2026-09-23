// GENERATED bởi tools/auto_promote.py — REVIEW TRƯỚC KHI MERGE.
// run_id: r-0032
// finding_id: f-sig-dom_unchanged
// task_id: t-101
// detected_by: implicit_signal:dom_unchanged
// evidence_sha256: dd3298fcb67d3e3a307ca32f576e36e521dd95e43f0777670c95afa925982237
// generated_at: 2026-09-22T18:10:17
//
// Chạy:  node tests_generated/promoted_f_sig_dom_unchanged.spec.mjs   (playwright + Chromium đã cài sẵn)

import { chromium } from 'playwright';
import assert from 'node:assert/strict';

const BASE = process.env.APP_BASE_URL || 'http://127.0.0.1:8000';

async function main() {
  const browser = await chromium.launch();
  const page = await browser.newPage();
  const title = 'qc-' + Math.random().toString(16).slice(2, 10);
  await page.goto(BASE + '/');
  await page.fill('#title', title);
  await page.fill('#body', 'auto-promote');
  await page.click('button[type=submit]');
  await page.waitForSelector(`#notes li:has-text('${title}')`);
  await page.click(`#notes li:has-text('${title}') >> text=Xoá`);
  await page.waitForFunction(
    (t) => ![...document.querySelectorAll('#notes li')].some((li) => li.textContent.includes(t)),
    title, { timeout: 3000 });
  await browser.close();
}

main().catch((e) => { console.error(e); process.exit(1); });
