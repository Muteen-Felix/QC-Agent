"""Webhook thật (Slack/Discord/Telegram/generic) khi một run kết thúc. Không import từ core/.

Không có ALERT_WEBHOOK_URL -> chỉ ghi log (dashboard/_state/alerts.log), không raise. Gửi thật qua
urllib (giống job.sut_alive), timeout ngắn, KHÔNG BAO GIỜ raise ra ngoài on_finish. URL webhook là
secret nên không bao giờ ghi/ trả về nguyên văn (kể cả trong alerts.log hay qua API).
"""
from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request
from pathlib import Path

from dashboard import runs_reader

TIMEOUT_S = 5.0


def channel_for(url: str | None) -> str:
    if not url:
        return "none"
    host = url.split("//", 1)[-1].split("/", 1)[0].lower()
    if "hooks.slack.com" in host:
        return "slack"
    if "discord.com" in host or "discordapp.com" in host:
        return "discord"
    if "api.telegram.org" in host:
        return "telegram"
    return "generic"


def current_channel() -> str:
    return channel_for(os.environ.get("ALERT_WEBHOOK_URL"))


def _dashboard_url() -> str:
    return (os.environ.get("DASHBOARD_URL") or "http://127.0.0.1:8080").rstrip("/")


def _verdict_emoji(gate_verdict: str, exit_code: int | None) -> str:
    if exit_code is not None and exit_code not in (0, 1):
        return "⚠️"
    return {"PASS": "🟢", "FAIL": "🔴"}.get(gate_verdict, "⚠️")


def build_message(runs_dir: Path, run_id: str, exit_code: int | None, report_dir: Path | None = None) -> str | None:
    """Soạn message tổng hợp cho MỘT run (không bắn riêng từng finding). None nếu không đọc được run."""
    report_dir = report_dir or (runs_dir.parent / "midscene_run" / "report")
    detail = runs_reader.run_detail(runs_dir, run_id, report_dir)
    if detail is None:
        return None
    run = detail["run"]
    emoji = _verdict_emoji(run.get("gate_verdict", "UNKNOWN"), exit_code)
    lines = [
        f"{emoji} QC-Agent {run['run_id']}: gate {run.get('gate_verdict', 'UNKNOWN')}"
        f" (exit {exit_code}, gate {run.get('gating_pass')}/{run.get('gating_total')})",
    ]
    findings = detail.get("findings") or []
    if findings:
        first = findings[0]
        more = f" (+{len(findings) - 1} khác)" if len(findings) > 1 else ""
        lines.append(f"🔎 {len(findings)} finding discovery — vd: {first.get('title')}{more}")
    for c in detail.get("canary") or []:
        if not c.get("ok"):
            lines.append(f"🚨 canary: {c.get('message')}")
    lines.append(f"↗ {_dashboard_url()}/#run={run['run_id']}")
    return "\n".join(lines)


def _payload(channel: str, text: str) -> dict:
    if channel == "slack":
        return {"text": text}
    if channel == "discord":
        return {"content": text}
    if channel == "telegram":
        chat_id = os.environ.get("ALERT_TELEGRAM_CHAT_ID")
        return {"chat_id": chat_id, "text": text} if chat_id else {}
    return {"text": text}  # generic


def _log(state_dir: Path, entry: dict) -> None:
    state_dir.mkdir(parents=True, exist_ok=True)
    with (state_dir / "alerts.log").open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(entry, ensure_ascii=False) + "\n")


def send(url: str | None, text: str, state_dir: Path, run_id: str) -> dict:
    """Gửi webhook thật. Không bao giờ raise. Trả {'ok': bool, ...}. URL không bao giờ bị ghi ra ngoài."""
    channel = channel_for(url)
    entry = {"ts": time.strftime("%Y-%m-%dT%H:%M:%S"), "run_id": run_id, "channel": channel}
    if not url:
        entry["ok"] = False
        entry["error"] = "ALERT_WEBHOOK_URL chưa cấu hình"
        _log(state_dir, entry)
        return entry
    payload = _payload(channel, text)
    if channel == "telegram" and not payload:
        entry["ok"] = False
        entry["error"] = "thiếu ALERT_TELEGRAM_CHAT_ID"
        _log(state_dir, entry)
        return entry
    req = urllib.request.Request(
        url, data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"}, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT_S) as resp:
            entry["ok"], entry["status"] = 200 <= resp.status < 300, resp.status
    except urllib.error.HTTPError as e:
        entry["ok"], entry["status"] = False, e.code
    except (urllib.error.URLError, OSError, TimeoutError) as e:
        entry["ok"], entry["error"] = False, str(e)
    _log(state_dir, entry)
    return entry


def notify_run(runs_dir: Path, run_id: str | None, exit_code: int | None, state_dir: Path | None = None) -> dict:
    """Gọi từ Job.on_finish. Không bao giờ raise (Job._wait đã bọc try/except nhưng ở đây tự bảo vệ luôn)."""
    state_dir = state_dir or (Path(__file__).resolve().parent / "_state")
    if not run_id:
        return {"ok": False, "error": "không xác định được run_id mới"}
    try:
        text = build_message(runs_dir, run_id, exit_code)
    except Exception as e:  # đọc report hỏng không được làm rơi webhook/job
        text = None
        _log(state_dir, {"ts": time.strftime("%Y-%m-%dT%H:%M:%S"), "run_id": run_id,
                          "channel": current_channel(), "ok": False, "error": f"build_message: {e}"})
    if text is None:
        return {"ok": False, "error": "không đọc được report.json"}
    return send(os.environ.get("ALERT_WEBHOOK_URL"), text, state_dir, run_id)
