"""Thông báo webhook (Slack / Discord / Telegram / generic) khi một run kết thúc. Dùng chung cho CI (CLI) và executor (job web).

  ALERT_WEBHOOK_URL        URL webhook — BÍ MẬT: không bao giờ được ghi log, trả về, hay đưa vào message
  ALERT_TELEGRAM_CHAT_ID   bắt buộc khi URL là api.telegram.org
  DASHBOARD_URL            gốc URL của giao diện web để đính link

    python -m qc_agent.integrations.notify --run-dir runs/r-0001 [--exit-code N] [--label TEXT] [--link URL]

Không bao giờ raise và không bao giờ làm hỏng CI (CLI luôn exit 0 trừ khi dùng sai lệnh): thông báo chỉ là phụ.
NỘI DUNG DO SUT KIỂM SOÁT (tiêu đề finding, task id, thông điệp canary) là input KHÔNG TIN CẬY: được làm sạch trước khi vào message
(gộp một dòng, vô hiệu @mention/<!channel>, thoát markdown/HTML tuỳ kênh, cắt độ dài) để không giả mạo verdict hay ping cả kênh."""
from __future__ import annotations

import argparse
import json
import logging
import os
import re
import sys
import urllib.error
import urllib.request
from pathlib import Path
from urllib.parse import urlsplit

log = logging.getLogger("qc_agent.notify")
TIMEOUT_S = 5.0
MAX_ITEM = 160
# chỉ xoá ký tự điều khiển KHÔNG phải khoảng trắng; xuống dòng, tab, CR, U+2028... để split() gộp thành dấu cách
_CONTROL = re.compile(r"[\x00-\x08\x0e-\x1b\x7f]")
_DISCORD_MD = re.compile(r"([\\*_`~|>#\[\]()])")
ZWSP = "\u200b"  # zero-width space: chèn sau "@" để vô hiệu mention


def channel_for(url: str | None) -> str:
    if not url:
        return "none"
    host = (urlsplit(url).hostname or "").lower()
    if host == "hooks.slack.com":
        return "slack"
    if host in ("discord.com", "discordapp.com") or host.endswith((".discord.com", ".discordapp.com")):
        return "discord"
    if host == "api.telegram.org":
        return "telegram"
    return "generic"


def current_channel() -> str:
    return channel_for(os.environ.get("ALERT_WEBHOOK_URL"))


def clean(value, channel: str = "generic", limit: int = MAX_ITEM) -> str:
    """Làm sạch một mẩu văn bản không tin cậy để chèn vào message."""
    text = " ".join(_CONTROL.sub("", str(value)).split())  # một dòng: không chèn được dòng giả (vd. "VERDICT: PASS")
    if channel == "slack":
        text = text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")  # <!channel>, <@U123>, <http://x|link>
    if channel == "discord":
        text = _DISCORD_MD.sub(r"\\\1", text)  # markdown/link giả
    text = text.replace("@", "@" + ZWSP)  # @here @channel @everyone @user: vô hiệu ở mọi kênh
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _read(path: Path):
    try:
        return json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError):
        return None


def load_run(run_dir) -> dict | None:
    """report.json + finding của các task không chặn gate (đọc từ results/*.json). None nếu không phải thư mục run hợp lệ."""
    run_dir = Path(run_dir)
    report = _read(run_dir / "report.json")
    if not isinstance(report, dict):
        return None
    findings = []
    for path in sorted((run_dir / "results").glob("*.json")):
        result = _read(path)
        if not isinstance(result, dict) or (result.get("verdict") or {}).get("gating"):
            continue
        findings += [f.get("title") for f in result.get("findings") or [] if isinstance(f, dict) and f.get("title")]
    return {"report": report, "findings": findings}


_EMOJI = {"PASS": "🟢", "FAIL": "🔴", "YELLOW": "🟡"}


def build_message(run: dict, *, label: str, exit_code: int | None = None, status: str | None = None,
                  link: str | None = None, channel: str = "generic") -> str:
    report = run["report"]
    verdict = report.get("gate_verdict", "UNKNOWN")
    gating = report.get("deterministic_view") or []
    passed = sum(1 for g in gating if isinstance(g, dict) and g.get("status") == "pass")
    system_problem = exit_code not in (None, 0, 1) or status in ("cancelled", "timed_out")
    emoji = "⚠️" if system_problem else _EMOJI.get(verdict, "⚠️")
    head = f"{emoji} QC-Agent {clean(label, channel, 80)}: gate {clean(verdict, channel, 12)}"
    extra = [f"gate {passed}/{len(gating)}"]
    if exit_code is not None:
        extra.insert(0, f"exit {int(exit_code)}")
    if status and status not in ("succeeded", "failed"):
        extra.insert(0, clean(status, channel, 16))
    lines = [f"{head} ({', '.join(extra)})"]
    findings = run.get("findings") or []
    if findings:
        more = f" (+{len(findings) - 1} khác)" if len(findings) > 1 else ""
        lines.append(f"🔎 {len(findings)} finding discovery — vd: {clean(findings[0], channel)}{more}")
    for canary in report.get("canary") or []:
        if isinstance(canary, dict) and not canary.get("ok"):
            lines.append(f"🚨 canary: {clean(canary.get('message', ''), channel)}")
    if link and urlsplit(link).scheme in ("http", "https"):
        lines.append(f"↗ {link}")
    return "\n".join(lines)


def _payload(channel: str, text: str) -> dict:
    if channel == "slack":
        return {"text": text}
    if channel == "discord":
        return {"content": text, "allowed_mentions": {"parse": []}}  # chặn ping ngay cả khi lọt @mention
    if channel == "telegram":
        chat_id = os.environ.get("ALERT_TELEGRAM_CHAT_ID")
        return {"chat_id": chat_id, "text": text} if chat_id else {}  # không đặt parse_mode: văn bản thuần
    return {"text": text}


def send(url: str | None, text: str, label: str = "") -> dict:
    """Gửi webhook. Không bao giờ raise. Trả {'ok', 'channel', 'status'?, 'error'?}; URL không bao giờ nằm trong kết quả/log."""
    channel = channel_for(url)
    result = {"ok": False, "channel": channel, "label": label}
    if not url:
        result["error"] = "ALERT_WEBHOOK_URL chưa cấu hình"
    elif urlsplit(url).scheme not in ("http", "https"):
        result["error"] = "ALERT_WEBHOOK_URL phải là http(s)"
    else:
        payload = _payload(channel, text)
        if channel == "telegram" and not payload:
            result["error"] = "thiếu ALERT_TELEGRAM_CHAT_ID"
        else:
            request = urllib.request.Request(url, data=json.dumps(payload).encode("utf-8"),
                                             headers={"Content-Type": "application/json"}, method="POST")
            try:
                with urllib.request.urlopen(request, timeout=TIMEOUT_S) as response:
                    result["ok"], result["status"] = 200 <= response.status < 300, response.status
            except urllib.error.HTTPError as error:
                result["status"] = error.code
            except (urllib.error.URLError, OSError, TimeoutError, ValueError) as error:
                result["error"] = type(error).__name__  # không đưa str(error): có thể chứa URL
    log.info("notify channel=%s label=%s ok=%s status=%s error=%s", channel, label, result["ok"], result.get("status"), result.get("error"))
    return result


def notify_run(run_dir, *, exit_code: int | None = None, label: str | None = None, link: str | None = None,
               status: str | None = None, verdict: str | None = None, url: str | None = None) -> dict:
    """Soạn và gửi thông báo cho MỘT run. Không bao giờ raise."""
    url = url if url is not None else os.environ.get("ALERT_WEBHOOK_URL")
    label = label or Path(run_dir).name
    try:
        run = load_run(run_dir)
        if run is None and (status or verdict):  # job bị huỷ/timeout/lỗi hệ thống trước khi có report: vẫn báo trạng thái
            run = {"report": {"gate_verdict": verdict or "UNKNOWN"}, "findings": []}
        if run is None:
            return {"ok": False, "channel": channel_for(url), "label": label, "error": "không đọc được report.json"}
        link = link or os.environ.get("DASHBOARD_URL")
        text = build_message(run, label=label, exit_code=exit_code, status=status, link=link, channel=channel_for(url))
    except Exception as error:  # noqa: BLE001 — report hỏng không được làm rơi CI/job
        return {"ok": False, "channel": channel_for(url), "label": label, "error": f"build_message: {type(error).__name__}"}
    return send(url, text, label)


def main(argv: list[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--run-dir", required=True)
    ap.add_argument("--exit-code", type=int)
    ap.add_argument("--label")
    ap.add_argument("--link")
    args = ap.parse_args(argv)
    print(json.dumps(notify_run(args.run_dir, exit_code=args.exit_code, label=args.label, link=args.link), ensure_ascii=False))
    return 0  # thông báo hỏng không được làm đỏ CI


if __name__ == "__main__":
    sys.exit(main())
