import json
import shutil
import subprocess
from pathlib import Path

import pytest

from qc_agent.adapters._base import AdapterParseError
from qc_agent.adapters.playwright_adapter import PlaywrightAdapter

ROOT = Path(__file__).parents[1]
SAMPLES = ROOT / "tests" / "samples"


def spec(spec_file="integration/tier.spec.mjs", **inputs):
    return {"target": {"base_url": "http://sut:8000"}, "inputs": {"spec_file": spec_file, **inputs}}


def write_spec(tmp_path, text="import { test, expect } from './support.mjs';\ntest('filter_applied', async () => { expect(true).toBeTruthy(); });\n"):
    path = tmp_path / "integration" / "tier.spec.mjs"
    path.parent.mkdir()
    path.write_text(text, encoding="utf-8")
    return path


def parse(tmp_path, sample, events):
    shutil.copy(SAMPLES / sample, tmp_path / "pw.json")
    (tmp_path / "events.json").write_text(json.dumps(events), encoding="utf-8")
    return PlaywrightAdapter().parse_output(subprocess.CompletedProcess([], 0, "out", ""), tmp_path, spec())


def test_parse_pass(tmp_path):
    out = parse(tmp_path, "playwright_report.pass.json", {"blocked_hosts": [], "har_missing": []})
    assert out.signals["checks"] == {"filter_applied": True, "report_downloaded": True, "no_external_requests": True}
    assert out.tokens == 0 and out.usd == 0.0


def test_parse_failure_skip_and_guard_events(tmp_path):
    out = parse(tmp_path, "playwright_report.fail_skip.json",
                {"blocked_hosts": ["example.org"], "har_missing": ["https://b.test/report?q=secret"]})
    assert out.signals["checks"] == {"filter_applied": False, "no_external_requests": False}
    assert "report_downloaded" not in out.signals["checks"]
    assert all("secret" not in finding["title"] for finding in out.findings)


@pytest.mark.parametrize("missing", ["pw.json", "events.json"])
def test_missing_output_is_error(tmp_path, missing):
    (tmp_path / "pw.json").write_text('{"suites": []}', encoding="utf-8")
    (tmp_path / "events.json").write_text('{"blocked_hosts": [], "har_missing": []}', encoding="utf-8")
    (tmp_path / missing).unlink()
    with pytest.raises(AdapterParseError):
        PlaywrightAdapter().parse_output(subprocess.CompletedProcess([], 0, "", ""), tmp_path, spec())


@pytest.mark.parametrize("text, message", [
    ("import { test } from '@playwright/test'; test('ok',()=>{});", "import Playwright"),
    ("import { test } from './support.mjs'; test('bad name',()=>{});", "snake_case"),
    ("import { test } from './support.mjs'; routeFromHAR('x'); test('ok',()=>{});", "routeFromHAR"),
    ("import { test } from './support.mjs'; const x={update: true}; test('ok',()=>{});", "update"),
])
def test_lint_rejects_guard_bypass(tmp_path, monkeypatch, text, message):
    write_spec(tmp_path, text)
    monkeypatch.chdir(tmp_path)
    with pytest.raises(AdapterParseError, match=message):
        PlaywrightAdapter().build_cmd(spec(), tmp_path / "work")


def test_build_command_sets_fail_closed_environment(tmp_path, monkeypatch):
    write_spec(tmp_path)
    har = tmp_path / "recording.har"
    har.write_text('{}', encoding="utf-8")
    work = tmp_path / "work"
    work.mkdir()
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(shutil, "which", lambda name: "/usr/bin/npx")
    adapter = PlaywrightAdapter()
    cmd = adapter.build_cmd(spec(har="recording.har", b_host="b.example"), work)
    assert cmd[:4] == ["/usr/bin/npx", "playwright", "test", "integration/tier.spec.mjs"]
    assert adapter.env["QC_B_HOST"] == "b.example" and adapter.env["QC_HAR_FILE"] == str(har.resolve())
    assert adapter.env["QC_ALLOW_HOSTS"] == "sut"


def test_path_escape_and_har_without_host_are_rejected(tmp_path, monkeypatch):
    write_spec(tmp_path)
    (tmp_path / "recording.har").write_text('{}', encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    with pytest.raises(AdapterParseError):
        PlaywrightAdapter().build_cmd(spec("../evil.spec.mjs"), tmp_path / "w")
    with pytest.raises(AdapterParseError, match="b_host"):
        PlaywrightAdapter().build_cmd(spec(har="recording.har"), tmp_path / "w")
