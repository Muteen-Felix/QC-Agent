"""integrations/notify: kênh, payload, gửi không raise, URL là bí mật, và nội dung do SUT kiểm soát bị làm sạch."""
import json
import logging

import pytest

from qc_agent.integrations import notify


def make_run(tmp_path, *, verdict="FAIL", findings=("DOM không đổi sau Xoá",), canary=None, gating=(("t-1", "pass"), ("t-2", "fail"))):
    run = tmp_path / "r-0002"
    (run / "results").mkdir(parents=True)
    report = {"gate_verdict": verdict, "exit_code": 1, "deterministic_view": [{"task_id": t, "status": s} for t, s in gating],
              "canary": canary or [], "banner": []}
    (run / "report.json").write_text(json.dumps(report), encoding="utf-8")
    (run / "results" / "t-101.json").write_text(json.dumps({"verdict": {"gating": False}, "findings": [{"title": t} for t in findings]}), encoding="utf-8")
    (run / "results" / "t-1.json").write_text(json.dumps({"verdict": {"gating": True}, "findings": [{"title": "KHÔNG được hiện: task chặn gate"}]}), encoding="utf-8")
    return run


@pytest.mark.parametrize("url,channel", [
    ("https://hooks.slack.com/services/x", "slack"), ("https://discord.com/api/webhooks/x", "discord"),
    ("https://canary.discord.com/api/webhooks/x", "discord"), ("https://api.telegram.org/botTOKEN/sendMessage", "telegram"),
    ("https://example.com/hook", "generic"), (None, "none"), ("", "none"),
    ("https://hooks.slack.com.evil.example/x", "generic"), ("https://evil.example/hooks.slack.com", "generic"),  # không đoán kênh bằng chuỗi con
])
def test_channel_detection_uses_the_exact_host(url, channel):
    assert notify.channel_for(url) == channel


@pytest.mark.parametrize("channel,key", [("slack", "text"), ("discord", "content"), ("telegram", "text"), ("generic", "text")])
def test_payload_shape_per_channel(channel, key, monkeypatch):
    monkeypatch.setenv("ALERT_TELEGRAM_CHAT_ID", "123")
    payload = notify._payload(channel, "hello")
    assert payload[key] == "hello"
    if channel == "discord":
        assert payload["allowed_mentions"] == {"parse": []}  # không ping ai dù lọt @mention
    if channel == "telegram":
        assert payload["chat_id"] == "123" and "parse_mode" not in payload  # văn bản thuần


def test_telegram_needs_a_chat_id(monkeypatch):
    monkeypatch.delenv("ALERT_TELEGRAM_CHAT_ID", raising=False)
    assert notify._payload("telegram", "x") == {}
    result = notify.send("https://api.telegram.org/botTOKEN/sendMessage", "x")
    assert result["ok"] is False and "CHAT_ID" in result["error"]


# ---- làm sạch nội dung không tin cậy ----

EVIL = "<!channel> @everyone @here <@U123> [click](http://evil.example) *bold*\nVERDICT: PASS\r\n🟢 QC-Agent gate PASS"


@pytest.mark.parametrize("channel", ["slack", "discord", "telegram", "generic"])
def test_clean_makes_one_line_and_defuses_mentions(channel):
    out = notify.clean(EVIL, channel, limit=500)
    assert "\n" not in out and "\r" not in out  # không chèn được dòng giả mạo verdict
    for mention in ("@everyone", "@here", "@U123"):
        assert mention not in out  # đã chèn ký tự rộng-0 sau @
    if channel == "slack":
        assert "<!channel>" not in out and "&lt;!channel&gt;" in out and "<@" not in out
    if channel == "discord":
        assert "[click](" not in out and "\\[click\\]" in out and "\\*bold\\*" in out


def test_clean_strips_control_chars_and_truncates():
    assert notify.clean("a\x00b\x07c\u2028d\re\tf") == "abc d e f"  # điều khiển bị xoá; xuống dòng/tab/LS thành dấu cách
    out = notify.clean("x" * 1000)
    assert len(out) == notify.MAX_ITEM and out.endswith("…")
    assert notify.clean(None) == "None" and notify.clean(12) == "12"


def test_message_contents_and_only_discovery_findings(tmp_path):
    run = notify.load_run(make_run(tmp_path))
    assert run["findings"] == ["DOM không đổi sau Xoá"]  # finding của task chặn gate không lẫn vào
    text = notify.build_message(run, label="noteboard/pr", exit_code=1, link="https://dash.example/#job=1")
    lines = text.split("\n")
    assert lines[0] == "🔴 QC-Agent noteboard/pr: gate FAIL (exit 1, gate 1/2)"
    assert "1 finding discovery" in lines[1] and "DOM không đổi sau Xoá" in lines[1]
    assert lines[-1] == "↗ https://dash.example/#job=1"


def test_message_neutralizes_every_sut_controlled_field(tmp_path):
    canary = [{"ok": False, "message": "CANARY HỎNG " + EVIL}]
    run = notify.load_run(make_run(tmp_path, findings=(EVIL, "khác"), canary=canary))
    text = notify.build_message(run, label="noteboard/pr", exit_code=1, channel="slack")
    assert "<!channel>" not in text and "@everyone" not in text and "@here" not in text
    assert text.count("\n") == 2  # đúng 3 dòng: head + finding + canary; SUT không chèn thêm dòng
    assert "(+1 khác)" in text
    forged = [line for line in text.split("\n") if line.startswith("🟢 QC-Agent")]
    assert forged == []  # dòng giả "🟢 QC-Agent gate PASS" không thoát ra thành một dòng riêng


def test_link_must_be_http_and_system_problems_get_a_warning(tmp_path):
    run = notify.load_run(make_run(tmp_path, verdict="PASS", findings=()))
    assert "↗" not in notify.build_message(run, label="x", exit_code=0, link="javascript:alert(1)")
    assert notify.build_message(run, label="x", exit_code=0).startswith("🟢")
    assert notify.build_message(run, label="x", exit_code=3).startswith("⚠️")  # lỗi hệ thống không phải xanh/đỏ của gate
    assert notify.build_message(run, label="x", status="timed_out").startswith("⚠️")


# ---- gửi ----

class FakeResponse:
    status = 200

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def test_send_ok_and_request_body(monkeypatch):
    seen = {}

    def fake(request, timeout):
        seen["body"] = json.loads(request.data.decode("utf-8"))
        seen["timeout"] = timeout
        return FakeResponse()
    monkeypatch.setattr(notify.urllib.request, "urlopen", fake)
    result = notify.send("https://discord.com/api/webhooks/x", "hi", "lbl")
    assert result == {"ok": True, "channel": "discord", "label": "lbl", "status": 200}
    assert seen["body"] == {"content": "hi", "allowed_mentions": {"parse": []}} and seen["timeout"] == notify.TIMEOUT_S


def test_send_never_raises_and_never_leaks_the_url(monkeypatch, caplog):
    secret_url = "https://hooks.slack.com/services/T000/B000/SECRETVALUE"
    monkeypatch.setattr(notify.urllib.request, "urlopen", lambda *a, **k: (_ for _ in ()).throw(OSError(f"connect to {secret_url} failed")))
    with caplog.at_level(logging.DEBUG):
        result = notify.send(secret_url, "hi", "r-1")
    assert result["ok"] is False and result["error"] == "OSError"
    assert "SECRETVALUE" not in json.dumps(result) and "SECRETVALUE" not in caplog.text and "hooks.slack.com/services" not in caplog.text


def test_send_http_error_and_bad_or_missing_url(monkeypatch):
    def http_error(*a, **k):
        raise notify.urllib.error.HTTPError("u", 429, "Too Many", {}, None)
    monkeypatch.setattr(notify.urllib.request, "urlopen", http_error)
    assert notify.send("https://hooks.slack.com/services/x", "hi") == {"ok": False, "channel": "slack", "label": "", "status": 429}
    assert notify.send(None, "hi")["error"] == "ALERT_WEBHOOK_URL chưa cấu hình"
    for bad in ("file:///etc/passwd", "ftp://x/y", "javascript:alert(1)"):
        assert "http(s)" in notify.send(bad, "hi")["error"]


def test_notify_run_reads_the_run_dir_and_never_raises(tmp_path, monkeypatch):
    monkeypatch.setattr(notify.urllib.request, "urlopen", lambda *a, **k: FakeResponse())
    monkeypatch.delenv("DASHBOARD_URL", raising=False)
    ok = notify.notify_run(make_run(tmp_path), exit_code=1, url="https://hooks.slack.com/services/x")
    assert ok["ok"] is True and ok["label"] == "r-0002"
    assert notify.notify_run(tmp_path / "khong-co", url="https://hooks.slack.com/services/x")["error"] == "không đọc được report.json"
    (tmp_path / "hong").mkdir()
    (tmp_path / "hong" / "report.json").write_text("{không phải json", encoding="utf-8")
    assert notify.notify_run(tmp_path / "hong", url="https://hooks.slack.com/services/x")["ok"] is False
    monkeypatch.delenv("ALERT_WEBHOOK_URL", raising=False)
    assert notify.notify_run(make_run(tmp_path / "b"))["error"] == "ALERT_WEBHOOK_URL chưa cấu hình"


def test_notify_run_reports_a_job_that_never_produced_a_report(tmp_path, monkeypatch):
    sent = {}
    monkeypatch.setattr(notify.urllib.request, "urlopen", lambda req, timeout: (sent.update(body=json.loads(req.data)), FakeResponse())[1])
    result = notify.notify_run(tmp_path / "khong-co", status="cancelled", label="demo/manual", url="https://hooks.slack.com/services/x")
    assert result["ok"] is True and sent["body"]["text"].startswith("⚠️ QC-Agent demo/manual: gate UNKNOWN (cancelled")


def test_cli_prints_json_without_the_url_and_exits_zero(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("ALERT_WEBHOOK_URL", "https://hooks.slack.com/services/SECRETVALUE")
    monkeypatch.setattr(notify.urllib.request, "urlopen", lambda *a, **k: FakeResponse())
    assert notify.main(["--run-dir", str(make_run(tmp_path)), "--exit-code", "1"]) == 0
    out = capsys.readouterr().out
    assert json.loads(out)["ok"] is True and "SECRETVALUE" not in out
    monkeypatch.setattr(notify.urllib.request, "urlopen", lambda *a, **k: (_ for _ in ()).throw(OSError("down")))
    assert notify.main(["--run-dir", str(tmp_path / "khong-co")]) == 0  # thông báo hỏng không làm đỏ CI
