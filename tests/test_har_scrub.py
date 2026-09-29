"""tools/har_scrub.py: lọc credential khỏi HAR, kiểm ngược bằng find_leaks, CLI fail closed.
Mọi "secret" trong test được dựng lúc chạy (JWT) hoặc là chuỗi giả rõ ràng — không commit chuỗi giống token thật."""
import base64
import copy
import json
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, "tools")
import har_scrub  # noqa: E402


def b64url(obj) -> str:
    return base64.urlsafe_b64encode(json.dumps(obj).encode()).decode().rstrip("=")


JWT = f"{b64url({'alg': 'HS256', 'typ': 'JWT'})}.{b64url({'sub': 'nguoi-dung-gia'})}.{b64url('chu-ky-gia')}"
assert JWT.startswith("eyJ")

SECRETS = {
    "bearer": "Bearer opaque-token-value-123456",
    "session": "SESSION-VALUE-AAA111",
    "cookie_req": "COOKIE-REQ-BBB222",
    "cookie_resp": "COOKIE-RESP-CCC333",
    "setcookie": "SETCOOKIE-DDD444",
    "xtoken": "XTOKEN-EEE555",
    "apikey_hdr": "APIKEYHDR-FFF666",
    "q_token": "QTOKEN-GGG777",
    "q_key": "QKEY-HHH888",
    "form_pw": "FORMPW-III999",
    "json_pw": "JSONPW-JJJ000",
    "json_otp": "OTP-KKK111",
    "resp_secret": "RESPSECRET-LLL222",
    "html_csrf": "HTMLCSRF-MMM333",
    "multipart": "MULTIPART-NNN444",
    "userinfo": "USERPW-OOO555",
    "referer_tok": "REFTOK-PPP666",
    "custom": "CUSTOMFIELD-QQQ777",
    "extra": "MASOTHUE-0123456789",
}


def make_har() -> dict:
    s = SECRETS
    url = f"https://user:{s['userinfo']}@example.gov.test/api/search?page=2&token={s['q_token']}&api_key={s['q_key']}"
    return {"log": {"version": "1.2", "creator": {"name": "test", "version": "1"}, "entries": [
        {
            "startedDateTime": "2026-01-01T00:00:00Z", "time": 12,
            "_initiator": {"type": "script", "url": f"https://example.gov.test/app.js?sid={s['session']}", "_token": s["custom"]},
            "request": {
                "method": "POST", "url": url, "httpVersion": "HTTP/1.1",
                "headers": [
                    {"name": "Authorization", "value": s["bearer"]},
                    {"name": "Cookie", "value": f"sid={s['cookie_req']}; theme=dark"},
                    {"name": "X-Auth-Token", "value": s["xtoken"]},
                    {"name": "x-api-key", "value": s["apikey_hdr"]},
                    {"name": "Referer", "value": f"https://example.gov.test/home?token={s['referer_tok']}"},
                    {"name": "Accept", "value": "application/json"},
                ],
                "cookies": [{"name": "sid", "value": s["cookie_req"]}, {"name": "theme", "value": "dark"}],
                "queryString": [{"name": "page", "value": "2"}, {"name": "token", "value": s["q_token"]}, {"name": "api_key", "value": s["q_key"]}],
                "postData": {"mimeType": "application/json", "text": json.dumps(
                    {"username": "u1", "password": s["json_pw"], "profile": {"otp": s["json_otp"], "note": "giữ nguyên"}, "maso_thue": s["extra"]})},
            },
            "response": {
                "status": 200, "statusText": "OK", "httpVersion": "HTTP/1.1",
                "headers": [{"name": "Set-Cookie", "value": f"sid={s['setcookie']}; HttpOnly"}, {"name": "Content-Type", "value": "application/json"}],
                "cookies": [{"name": "sid", "value": s["cookie_resp"]}],
                "content": {"mimeType": "application/json", "size": 100, "text": json.dumps(
                    {"ok": True, "access_token": JWT, "client_secret": s["resp_secret"], "items": [{"id": 1, "authHeader": f"Bearer {JWT}"}], "code": 200})},
                "redirectURL": "",
            },
        },
        {
            "request": {
                "method": "POST", "url": "https://example.gov.test/login", "headers": [], "cookies": [], "queryString": [],
                "postData": {"mimeType": "application/x-www-form-urlencoded", "params": [{"name": "password", "value": s["form_pw"]}, {"name": "lang", "value": "vi"}],
                            "text": f"lang=vi&password={s['form_pw']}"},
            },
            "response": {"status": 302, "headers": [{"name": "Location", "value": f"https://example.gov.test/app#access_token={s['referer_tok']}"}],
                         "cookies": [], "content": {"mimeType": "text/html", "text":
                             f'<form><input type="hidden" name="csrf_token" value="{s["html_csrf"]}"><input name="q" value="giữ"></form>'},
                         "redirectURL": f"https://example.gov.test/app?code={s['referer_tok']}"},
        },
        {
            "request": {"method": "POST", "url": "https://example.gov.test/upload", "headers": [], "cookies": [], "queryString": [],
                        "postData": {"mimeType": "multipart/form-data; boundary=XX", "text":
                            f'--XX\r\nContent-Disposition: form-data; name="password"\r\n\r\n{s["multipart"]}\r\n--XX\r\nContent-Disposition: form-data; name="title"\r\n\r\ngiữ\r\n--XX--\r\n'}},
            "response": {"status": 200, "headers": [], "cookies": [], "content": {"mimeType": "application/json", "encoding": "base64",
                         "text": base64.b64encode(json.dumps({"password": s["json_pw"]}).encode()).decode()}},
        },
    ]}}


def dump(har) -> str:
    return json.dumps(har, ensure_ascii=False)


EXTRA = ("maso_thue",)


def test_no_original_secret_survives():
    cleaned = har_scrub.scrub(make_har(), extra_redact_keys=EXTRA)
    text = dump(cleaned)
    assert JWT not in text and har_scrub._JWT.search(text) is None       # (không so "eyJ" trần: base64 của `{"` luôn bắt đầu như vậy)
    for name, secret in SECRETS.items():
        assert secret not in text, name
    assert har_scrub.find_leaks(cleaned) == []


def test_original_is_reported_as_leaky():
    leaks = har_scrub.find_leaks(make_har())
    assert leaks and all(SECRETS[k] not in " ".join(leaks) for k in SECRETS) and JWT not in " ".join(leaks)   # mô tả không chứa giá trị


def test_structure_stays_valid_and_benign_data_is_kept():
    original = make_har()
    cleaned = json.loads(dump(har_scrub.scrub(original, extra_redact_keys=EXTRA)))      # còn là JSON hợp lệ
    assert len(cleaned["log"]["entries"]) == 3
    for entry in cleaned["log"]["entries"]:
        assert "request" in entry and "response" in entry
    first = cleaned["log"]["entries"][0]
    assert first["request"]["method"] == "POST" and first["response"]["status"] == 200
    assert {"name": "Accept", "value": "application/json"} in first["request"]["headers"]
    assert {"name": "page", "value": "2"} in first["request"]["queryString"] and "page=2" in first["request"]["url"]
    body = json.loads(first["request"]["postData"]["text"])
    assert body["username"] == "u1" and body["profile"]["note"] == "giữ nguyên" and body["password"] == "REDACTED"
    resp = json.loads(first["response"]["content"]["text"])
    assert resp["ok"] is True and resp["code"] == 200 and resp["access_token"] == "REDACTED"
    assert "input name=\"q\" value=\"giữ\"" in cleaned["log"]["entries"][1]["response"]["content"]["text"]
    assert "name=\"title\"\r\n\r\ngiữ" in cleaned["log"]["entries"][2]["request"]["postData"]["text"]


def test_headers_and_cookies():
    first = har_scrub.scrub(make_har())["log"]["entries"][0]
    headers = {h["name"]: h["value"] for h in first["request"]["headers"] + first["response"]["headers"]}
    for name in ("Authorization", "Cookie", "X-Auth-Token", "x-api-key", "Set-Cookie"):
        assert headers[name] == "REDACTED", name
    assert headers["Accept"] == "application/json"
    assert all(c["value"] == "REDACTED" for c in first["request"]["cookies"] + first["response"]["cookies"])
    assert "token=REDACTED" in headers["Referer"]           # header URL: chỉ tham số nhạy cảm bị lọc


def test_url_and_query_string_stay_in_sync():
    request = har_scrub.scrub(make_har())["log"]["entries"][0]["request"]
    assert request["url"] == "https://REDACTED@example.gov.test/api/search?page=2&token=REDACTED&api_key=REDACTED"
    assert {p["name"]: p["value"] for p in request["queryString"]} == {"page": "2", "token": "REDACTED", "api_key": "REDACTED"}


def test_fragment_redirect_and_form_bodies():
    second = har_scrub.scrub(make_har())["log"]["entries"][1]
    assert second["response"]["headers"][0]["value"].endswith("#access_token=REDACTED")
    assert second["response"]["redirectURL"].endswith("?code=REDACTED")
    assert second["request"]["postData"]["text"] == "lang=vi&password=REDACTED"
    assert second["request"]["postData"]["params"] == [{"name": "password", "value": "REDACTED"}, {"name": "lang", "value": "vi"}]


def test_base64_body_is_scrubbed_and_stays_base64():
    content = har_scrub.scrub(make_har())["log"]["entries"][2]["response"]["content"]
    assert json.loads(base64.b64decode(content["text"])) == {"password": "REDACTED"} and content["encoding"] == "base64"


def test_binary_base64_is_left_alone():
    har = {"log": {"entries": [{"request": {}, "response": {"content": {"encoding": "base64", "text": base64.b64encode(b"\xff\xfe\x00bin").decode()}}}]}}
    assert har_scrub.scrub(har) == har and har_scrub.find_leaks(har) == []


def test_idempotent():
    once = har_scrub.scrub(make_har(), extra_redact_keys=EXTRA)
    assert har_scrub.scrub(once, extra_redact_keys=EXTRA) == once


def test_input_is_not_mutated():
    original = make_har()
    snapshot = copy.deepcopy(original)
    cleaned = har_scrub.scrub(original, extra_redact_keys=EXTRA)
    assert original == snapshot and cleaned is not original
    cleaned["log"]["entries"][0]["request"]["headers"].append({"name": "X", "value": "y"})     # kết quả không dùng chung nút với đầu vào
    assert original == snapshot


def test_clean_har_is_unchanged():
    har = {"log": {"entries": [{"request": {"method": "GET", "url": "https://example.gov.test/a?page=1", "headers": [{"name": "Accept", "value": "*/*"}],
                                            "cookies": [], "queryString": [{"name": "page", "value": "1"}]},
                                "response": {"status": 200, "headers": [], "cookies": [], "content": {"mimeType": "text/plain", "text": "xin chào"}}}]}}
    assert har_scrub.scrub(har) == har and har_scrub.find_leaks(har) == []


def test_find_leaks_detects_a_reinserted_jwt_anywhere():
    cleaned = har_scrub.scrub(make_har(), extra_redact_keys=EXTRA)
    for where in (lambda h: h["log"]["entries"][0]["request"]["postData"].update(text=f'{{"note": "{JWT}"}}'),
                  lambda h: h["log"]["entries"][0].update(_note=f"dòng log {JWT}"),                       # trường lạ mà scrub không có đường riêng
                  lambda h: h["log"]["entries"][1]["response"]["headers"].append({"name": "X-Note", "value": f"Bearer {JWT}"})):
        dirty = copy.deepcopy(cleaned)
        where(dirty)
        leaks = har_scrub.find_leaks(dirty)
        assert leaks and JWT not in " ".join(leaks)


def test_find_leaks_detects_unredacted_header_cookie_and_param():
    cleaned = har_scrub.scrub(make_har())
    entry = cleaned["log"]["entries"][0]
    entry["request"]["headers"][0]["value"] = "Basic abc"
    entry["request"]["cookies"][0]["value"] = "x"
    entry["request"]["queryString"][1]["value"] = "y"
    leaks = " | ".join(har_scrub.find_leaks(cleaned))
    assert "header Authorization" in leaks and "cookie sid" in leaks and "tham số token" in leaks


def test_extra_redact_keys():
    har = {"log": {"entries": [{"request": {"method": "GET", "url": "https://h.test/x?ma_so=123", "queryString": [{"name": "ma_so", "value": "123"}], "headers": [{"name": "X-Ma-So", "value": "9"}]},
                                "response": {"status": 200}}]}}
    cleaned = har_scrub.scrub(har, extra_redact_keys=("ma_so", "x-ma-so"))
    request = cleaned["log"]["entries"][0]["request"]
    assert request["url"].endswith("ma_so=REDACTED") and request["queryString"][0]["value"] == "REDACTED" and request["headers"][0]["value"] == "REDACTED"
    assert har_scrub.scrub(har) == har     # không khai extra thì tên lạ được giữ


@pytest.mark.parametrize("name,expected", [
    ("password", True), ("newPassword", True), ("access_token", True), ("accessToken", True), ("X-CSRF-Token", True), ("csrfmiddlewaretoken", True),
    ("client_secret", True), ("otp", True), ("sessionId", True), ("captcha", True),
    ("page", False), ("username", False), ("passport", False), ("bypass", False), ("keyword", False), ("status", False),
])
def test_body_key_matching(name, expected):
    assert har_scrub._is_secret_key(name, har_scrub._BODY_WORDS) is expected


# ---- CLI: fail closed ----

def write_har(path: Path, har: dict) -> Path:
    path.write_text(json.dumps(har, ensure_ascii=False), encoding="utf-8")
    return path


def test_cli_writes_clean_file(tmp_path, capsys):
    src, out = write_har(tmp_path / "in.har", make_har()), tmp_path / "out.har"
    assert har_scrub.main([str(src), str(out), "--extra-key", "maso_thue"]) == 0
    written = out.read_text(encoding="utf-8")
    assert all(secret not in written for secret in SECRETS.values()) and JWT not in written
    assert har_scrub.find_leaks(json.loads(written)) == []
    assert json.loads(src.read_text(encoding="utf-8")) == make_har()          # file đầu vào không bị đụng


def test_cli_fails_closed_when_leak_remains(tmp_path, capsys, monkeypatch):
    """scrub bị 'quên' một chỗ (giả lập bằng cách vô hiệu hoá nó): CLI phải thoát 1 và KHÔNG tạo file đầu ra."""
    src, out = write_har(tmp_path / "in.har", make_har()), tmp_path / "out.har"
    monkeypatch.setattr(har_scrub, "scrub", lambda har, **kw: copy.deepcopy(har))
    assert har_scrub.main([str(src), str(out)]) == 1
    assert not out.exists() and not list(tmp_path.glob(".har-scrub-*"))
    err = capsys.readouterr().err
    assert "KHÔNG ghi" in err and JWT not in err and all(secret not in err for secret in SECRETS.values())


def test_cli_fail_closed_keeps_existing_output(tmp_path, monkeypatch):
    src, out = write_har(tmp_path / "in.har", make_har()), tmp_path / "out.har"
    out.write_text("BẢN CŨ", encoding="utf-8")
    monkeypatch.setattr(har_scrub, "scrub", lambda har, **kw: copy.deepcopy(har))
    assert har_scrub.main([str(src), str(out)]) == 1
    assert out.read_text(encoding="utf-8") == "BẢN CŨ"


@pytest.mark.parametrize("content", ["không phải json", "[]", '{"log": {}}'])
def test_cli_rejects_non_har_input(tmp_path, content):
    src, out = tmp_path / "in.har", tmp_path / "out.har"
    src.write_text(content, encoding="utf-8")
    assert har_scrub.main([str(src), str(out)]) == 2 and not out.exists()


def test_cli_missing_input(tmp_path):
    assert har_scrub.main([str(tmp_path / "nope.har"), str(tmp_path / "out.har")]) == 2


def test_cli_as_script_exit_codes(tmp_path):
    """Đúng lệnh mà Làn B sẽ chạy: `python tools/har_scrub.py IN OUT`."""
    src, out = write_har(tmp_path / "in.har", make_har()), tmp_path / "out.har"
    done = subprocess.run([sys.executable, "tools/har_scrub.py", str(src), str(out)], capture_output=True, text=True, encoding="utf-8")
    assert done.returncode == 0, done.stderr
    assert har_scrub.find_leaks(json.loads(out.read_text(encoding="utf-8"))) == []
