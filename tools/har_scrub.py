"""Lọc credential khỏi file HAR trước khi commit (Làn A, A-5). Chỉ dùng thư viện chuẩn.

    python tools/har_scrub.py IN.har OUT.har [--extra-key TÊN ...]
    exit 0 = đã ghi OUT · 1 = còn sót credential sau khi lọc (FAIL CLOSED: KHÔNG ghi OUT, không đụng file OUT đã có) · 2 = IN không đọc được/không phải HAR

    from har_scrub import scrub, find_leaks        # sys.path.insert(0, "tools")
    clean = scrub(har)                             # HAR mới, giá trị nhạy cảm -> "REDACTED"; `har` đầu vào không bị sửa
    assert find_leaks(clean) == []                 # kiểm ngược: mô tả chỗ còn sót (không chứa giá trị)

Hai nửa độc lập: `scrub` đi theo cấu trúc HAR (header, cookie, queryString, postData, content); `find_leaks` KHÔNG tin cấu trúc đó —
nó duyệt MỌI nút của cây (kể cả trường lạ như `_initiator`) và quét lại bằng mẫu nhận diện. Chỗ scrub quên đi qua thì find_leaks vẫn thấy.

Giới hạn (nói thẳng): lọc CREDENTIAL, không lọc dữ liệu cá nhân trong `response.content.text`; nội dung nhị phân (base64 không giải được UTF-8)
không kiểm được; JSON trong thân có thể bị dump lại (khoảng trắng đổi) khi có giá trị bị lọc. Người thứ hai vẫn phải xem HAR bằng mắt.
"""
from __future__ import annotations

import argparse
import base64
import binascii
import copy
import json
import os
import re
import sys
import tempfile
from pathlib import Path
from urllib.parse import unquote_plus, urlsplit, urlunsplit

REDACTED = "REDACTED"

_HEADERS = {"authorization", "cookie", "set-cookie", "proxy-authorization"}
_X_HEADER = re.compile(r"^x-.*(token|key|secret|auth)", re.I)
# Tên khoá nhạy cảm: khớp theo TỪ (tách bằng ký tự lạ và camelCase) hoặc chuỗi con của dạng đã bỏ dấu phân cách.
_SECRET_SUBSTR = ("token", "password", "passwd", "secret", "csrf", "xsrf", "captcha", "sessionid", "apikey", "credential")
_BODY_WORDS = frozenset({"pass", "pwd", "otp", "jwt", "sid", "session", "auth", "authorization"})    # JSON/HTML/XML: `key`, `code` quá chung
_URL_WORDS = _BODY_WORDS | {"key", "code", "signature", "sig"}                                       # query string, form-urlencoded
_WORD_SPLIT = re.compile(r"[^A-Za-z0-9]+")
_CAMEL = re.compile(r"(?<=[a-z0-9])(?=[A-Z])")

_JWT = re.compile(r"(?<![A-Za-z0-9_-])eyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]*")
_BEARER = re.compile(r"(?i)\b(bearer)\s+(?!REDACTED\b)[A-Za-z0-9._~+/=-]{8,}")
_URLISH = re.compile(r"(?i)^(?:https?|wss?|ftp)://")
_FORM = re.compile(r"[^\s=&]+=[^\s&]*(?:&[^\s=&]+=[^\s&]*)*")

_HTML_INPUT = re.compile(r"<input\b[^>]*>", re.I)
_HTML_NAME = re.compile(r"""(?<![\w-])name\s*=\s*["']?([^"'\s>]+)""", re.I)
_HTML_PASSWORD = re.compile(r"""(?<![\w-])type\s*=\s*["']?password""", re.I)
_HTML_VALUE = re.compile(r"""((?<![\w-])value\s*=\s*)(["'])(.*?)\2""", re.I | re.S)
_MULTIPART = re.compile(r'(Content-Disposition:[^\r\n]*?\bname="([^"]*)"[^\r\n]*(?:\r?\n(?!\r?\n)[^\r\n]*)*\r?\n\r?\n)((?:(?!\r?\n--)[\s\S])*)', re.I)
_JSONISH = re.compile(r'"((?:[^"\\]|\\.)+)"(\s*:\s*)("(?:[^"\\]|\\.)*"|[^\s,}\]"{\[][^,}\]]*)')
_XML = re.compile(r"<([A-Za-z_][\w.:-]*)>([^<]+)</\1>")


def _compact(name: str) -> str:
    return re.sub(r"[^a-z0-9]", "", name.lower())


def _normalize_extra(keys) -> frozenset:
    return frozenset(c for c in (_compact(k) for k in keys) if c)


def _needs(value) -> bool:
    """Giá trị còn đáng lọc? (rỗng/null/đã REDACTED thì không: giữ nguyên để idempotent)."""
    return value is not None and value != "" and value != REDACTED and value != [] and value != {}


def _is_secret_key(name: str, words, extra=frozenset()) -> bool:
    compact = _compact(name)
    if not compact:
        return False
    tokens = {w.lower() for w in _WORD_SPLIT.split(_CAMEL.sub("_", name)) if w}
    if compact in extra or tokens & extra:
        return True
    return bool(tokens & words) or any(s in compact for s in _SECRET_SUBSTR)


def _is_secret_header(name: str, extra=frozenset()) -> bool:
    lowered = name.strip().lower()
    return lowered in _HEADERS or bool(_X_HEADER.match(lowered)) or _compact(name) in extra


def _scrub_patterns(text: str) -> str:
    return _BEARER.sub(rf"\1 {REDACTED}", _JWT.sub(REDACTED, text))


def _scrub_pairs_raw(raw: str, words, extra) -> str:
    """`a=1&token=x` -> `a=1&token=REDACTED`, giữ nguyên phần còn lại (kể cả mã hoá %xx)."""
    out = []
    for piece in raw.split("&"):
        name, sep, value = piece.partition("=")
        if sep and _needs(value) and _is_secret_key(unquote_plus(name), words, extra):
            piece = f"{name}={REDACTED}"
        out.append(piece)
    return "&".join(out)


def _scrub_url(url: str, extra) -> str:
    try:
        parts = urlsplit(url)
    except ValueError:
        return _scrub_patterns(url)
    netloc = REDACTED + "@" + parts.netloc.rpartition("@")[2] if "@" in parts.netloc else parts.netloc
    query = _scrub_pairs_raw(parts.query, _URL_WORDS, extra)
    fragment = _scrub_pairs_raw(parts.fragment, _URL_WORDS, extra) if "=" in parts.fragment else parts.fragment
    if (netloc, query, fragment) != (parts.netloc, parts.query, parts.fragment):   # không đổi thì trả nguyên văn: urlunsplit có thể chuẩn hoá
        url = urlunsplit((parts.scheme, netloc, parts.path, query, fragment))
    return _scrub_patterns(url)


def _redact_json(node, extra):
    if isinstance(node, dict):
        return {k: REDACTED if _needs(v) and _is_secret_key(str(k), _BODY_WORDS, extra) else _redact_json(v, extra) for k, v in node.items()}
    if isinstance(node, list):
        return [_redact_json(v, extra) for v in node]
    return _scrub_patterns(node) if isinstance(node, str) else node


def _html_input(match, extra):
    tag = match.group(0)
    name = _HTML_NAME.search(tag)
    if not (_HTML_PASSWORD.search(tag) or (name and _is_secret_key(name.group(1), _BODY_WORDS, extra))):
        return tag
    return _HTML_VALUE.sub(lambda v: v.group(0) if not _needs(v.group(3)) else f"{v.group(1)}{v.group(2)}{REDACTED}{v.group(2)}", tag)


def _multipart(match, extra):
    if _is_secret_key(match.group(2), _BODY_WORDS, extra) and _needs(match.group(3)):
        return match.group(1) + REDACTED
    return match.group(0)


def _jsonish(match, extra):
    value = match.group(3)
    if _is_secret_key(unquote_plus(match.group(1)), _BODY_WORDS, extra) and _needs(value.strip('"')) and value != "null":
        return f'"{match.group(1)}"{match.group(2)}"{REDACTED}"'
    return match.group(0)


def _xml(match, extra):
    if _is_secret_key(match.group(1), _BODY_WORDS, extra) and _needs(match.group(2).strip()):
        return f"<{match.group(1)}>{REDACTED}</{match.group(1)}>"
    return match.group(0)


def _scrub_text_regex(text: str, extra) -> str:
    text = _HTML_INPUT.sub(lambda m: _html_input(m, extra), text)
    text = _MULTIPART.sub(lambda m: _multipart(m, extra), text)
    text = _JSONISH.sub(lambda m: _jsonish(m, extra), text)
    text = _XML.sub(lambda m: _xml(m, extra), text)
    return _scrub_patterns(text)


def _scrub_body(text: str, mime, extra) -> str:
    if text.lstrip("﻿ \t\r\n")[:1] in ("{", "["):
        try:
            obj = json.loads(text.lstrip("﻿"))
        except (ValueError, RecursionError):
            obj = None
        if isinstance(obj, (dict, list)):
            new = _redact_json(obj, extra)
            return text if new == obj else json.dumps(new, ensure_ascii=False)    # không có gì để lọc thì giữ nguyên định dạng gốc
    if "x-www-form-urlencoded" in (mime or "").lower() or _FORM.fullmatch(text):
        return _scrub_patterns(_scrub_pairs_raw(text, _URL_WORDS, extra))
    return _scrub_text_regex(text, extra)


def _scrub_leaf(text: str, extra=frozenset(), mime=None) -> str:
    """Lọc MỘT chuỗi bất kể nó nằm ở đâu trong HAR. Dùng cho cả scrub (lượt duyệt tổng quát) và find_leaks (chuỗi đổi ⇒ còn sót)."""
    return _scrub_url(text, extra) if _URLISH.match(text) else _scrub_body(text, mime, extra)


def _decode_b64(text: str):
    try:
        return base64.b64decode(text, validate=True).decode("utf-8")
    except (binascii.Error, ValueError):
        return None    # nhị phân: không kiểm được


def _scrub_named(items, secret, extra) -> None:
    for item in items if isinstance(items, list) else []:
        if isinstance(item, dict) and isinstance(item.get("name"), str) and _needs(item.get("value")) and secret(item["name"]):
            item["value"] = REDACTED


def _scrub_message(message, extra) -> None:
    if not isinstance(message, dict):
        return
    _scrub_named(message.get("headers"), lambda n: _is_secret_header(n, extra), extra)
    _scrub_named(message.get("cookies"), lambda n: True, extra)                      # cookie: lọc MỌI value
    _scrub_named(message.get("queryString"), lambda n: _is_secret_key(n, _URL_WORDS, extra), extra)
    post = message.get("postData")
    if isinstance(post, dict):
        _scrub_named(post.get("params"), lambda n: _is_secret_key(n, _URL_WORDS, extra), extra)
        if isinstance(post.get("text"), str):
            post["text"] = _scrub_body(post["text"], post.get("mimeType"), extra)
    content = message.get("content")
    if isinstance(content, dict) and isinstance(content.get("text"), str):
        if content.get("encoding") == "base64":
            decoded = _decode_b64(content["text"])
            if decoded is not None:
                cleaned = _scrub_body(decoded, content.get("mimeType"), extra)
                if cleaned != decoded:
                    content["text"] = base64.b64encode(cleaned.encode("utf-8")).decode("ascii")
        else:
            content["text"] = _scrub_body(content["text"], content.get("mimeType"), extra)


def _scrub_all_strings(node, extra):
    if isinstance(node, dict):    # khoá nhạy cảm mang giá trị đơn ở chỗ lạ (`_initiator._token`...) cũng bị lọc: cùng luật với find_leaks
        return {k: REDACTED if isinstance(v, (str, int, float, bool)) and _needs(v) and _is_secret_key(str(k), _BODY_WORDS, extra)
                else _scrub_all_strings(v, extra) for k, v in node.items()}
    if isinstance(node, list):
        return [_scrub_all_strings(v, extra) for v in node]
    return _scrub_leaf(node, extra) if isinstance(node, str) else node


def scrub(har: dict, *, extra_redact_keys: tuple[str, ...] = ()) -> dict:
    """Trả HAR mới, đã thay giá trị nhạy cảm bằng 'REDACTED'. Không sửa `har` đầu vào. Cấu trúc HAR giữ nguyên hợp lệ."""
    extra = _normalize_extra(extra_redact_keys)
    out = copy.deepcopy(har)
    log = out.get("log") if isinstance(out, dict) else None
    entries = log.get("entries") if isinstance(log, dict) else None
    for entry in entries if isinstance(entries, list) else []:
        if isinstance(entry, dict):
            _scrub_message(entry.get("request"), extra)
            _scrub_message(entry.get("response"), extra)
    return _scrub_all_strings(out, extra)      # lượt tổng quát: URL, JWT, Bearer ở BẤT KỲ chuỗi nào (Referer, Location, redirectURL, _initiator...)


def find_leaks(har: dict) -> list[str]:
    """Danh sách mô tả chỗ còn sót (không chứa giá trị). Rỗng = sạch."""
    leaks: list[str] = []

    def walk(node, path: str) -> None:
        if isinstance(node, dict):
            name, value = node.get("name"), node.get("value")
            if isinstance(name, str) and "value" in node and _needs(value):
                if "headers[" in path and _is_secret_header(name):
                    leaks.append(f"{path}: header {name} còn giá trị")
                elif "cookies[" in path:
                    leaks.append(f"{path}: cookie {name} còn giá trị")
                elif ("queryString[" in path or "params[" in path) and _is_secret_key(name, _URL_WORDS):
                    leaks.append(f"{path}: tham số {name} còn giá trị")
            if node.get("encoding") == "base64" and isinstance(node.get("text"), str):
                decoded = _decode_b64(node["text"])
                if decoded is not None and _scrub_leaf(decoded) != decoded:
                    leaks.append(f"{path}.text: nội dung base64 còn dữ liệu nhạy cảm")
            for key, child in node.items():
                child_path = f"{path}.{key}"
                if isinstance(child, (str, int, float, bool)) and _needs(child) and _is_secret_key(str(key), _BODY_WORDS):
                    leaks.append(f"{child_path}: khoá nhạy cảm còn giá trị")
                walk(child, child_path)
        elif isinstance(node, list):
            for index, child in enumerate(node):
                walk(child, f"{path}[{index}]")
        elif isinstance(node, str) and _scrub_leaf(node) != node:
            leaks.append(f"{path}: chuỗi còn JWT/Bearer/tham số URL/khoá nhạy cảm")

    walk(har, "$")
    return leaks


def _write_atomic(path: Path, text: str) -> None:
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".har-scrub-", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(text)
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


def main(argv: list[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):    # Windows không có console UTF-8: print() tiếng Việt ném UnicodeEncodeError, script "thành công" báo exit 1
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(prog="har_scrub.py", description="Lọc credential khỏi HAR; fail closed nếu còn sót.")
    ap.add_argument("input", help="HAR thô")
    ap.add_argument("output", help="HAR đã lọc (chỉ được ghi khi find_leaks rỗng)")
    ap.add_argument("--extra-key", action="append", default=[], metavar="TÊN", help="thêm tên khoá phải lọc (lặp được)")
    args = ap.parse_args(argv)
    try:
        har = json.loads(Path(args.input).read_text(encoding="utf-8-sig"))
        if not (isinstance(har, dict) and isinstance(har.get("log"), dict) and isinstance(har["log"].get("entries"), list)):
            raise ValueError("thiếu log.entries: không phải HAR")
    except (OSError, ValueError) as error:
        print(f"LỖI: không đọc được {args.input}: {error}", file=sys.stderr)
        return 2
    clean = scrub(har, extra_redact_keys=tuple(args.extra_key))
    leaks = find_leaks(clean)
    if leaks:
        print(f"LỖI: còn {len(leaks)} chỗ sót sau khi lọc, KHÔNG ghi {args.output}:", file=sys.stderr)
        for leak in leaks:
            print(f"  - {leak}", file=sys.stderr)
        return 1
    _write_atomic(Path(args.output), json.dumps(clean, ensure_ascii=False, indent=2) + "\n")
    print(f"đã ghi {args.output} ({len(clean['log']['entries'])} entry, không còn credential theo find_leaks)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
