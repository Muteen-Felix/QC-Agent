"""Máy chủ HTTP giả cho test tích hợp: GitHub API (check-runs, comment PR), webhook và Messages API của Anthropic (FakeAnthropic). Ghi lại mọi request để kiểm chứng."""
import json
import re
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer, ThreadingHTTPServer
from pathlib import Path


class FakeGitHub:
    def __init__(self, host: str = "127.0.0.1"):
        self.comments: list[dict] = []
        self.check_runs: list[dict] = []
        self.requests: list[dict] = []
        self.pr_files: list[dict] = []        # GET /pulls/N/files
        self.reviews: list[dict] = []         # POST/GET /pulls/N/reviews
        self.policy_files: dict[str, str] = {}   # configs/projects/<tên> của "qc-agent@main" mà contents API giả trả về
        self.main_sha = "a" * 40
        self.protection: dict | None = None      # GET /repos/o/r/branches/<b>/protection (dạng GET của GitHub); None => 404 "Branch not protected"
        self.protection_puts: list[dict] = []    # body của mọi PUT protection
        self.forced: dict[tuple[str, str], int] = {}  # (method, path-prefix) -> status lỗi buộc trả về
        self._next_id = 100
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def _send(self, status, payload=None):
                data = json.dumps(payload).encode("utf-8") if payload is not None else b""
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def _send_raw(self, status, text):
                data = text.encode("utf-8")
                self.send_response(status)
                self.send_header("Content-Type", "text/plain; charset=utf-8")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def _handle(self):
                length = int(self.headers.get("Content-Length") or 0)
                body = json.loads(self.rfile.read(length)) if length else None
                path = self.path
                outer.requests.append({"method": self.command, "path": path, "body": body, "auth": self.headers.get("Authorization"),
                                       "version": self.headers.get("X-GitHub-Api-Version")})
                for (method, prefix), status in outer.forced.items():
                    if method == self.command and path.startswith(prefix):
                        return self._send(status, {"message": "Resource not accessible by integration"})
                if self.command == "GET" and (m := re.match(r"^/repos/[^/]+/[^/]+/contents/configs/projects/([^/?]+)\?ref=main$", path)):
                    return self._send_raw(200, outer.policy_files[m.group(1)]) if m.group(1) in outer.policy_files else self._send(404, {"message": "Not Found"})
                if re.match(r"^/repos/[^/]+/[^/]+/branches/.+/protection$", path):
                    if self.command == "GET":
                        return self._send(200, outer.protection) if outer.protection is not None else self._send(404, {"message": "Branch not protected"})
                    if self.command == "PUT":
                        outer.protection_puts.append(body)
                        return self._send(200, body)
                if self.command == "GET" and re.match(r"^/repos/[^/]+/[^/]+/commits/main$", path):
                    return self._send_raw(200, outer.main_sha)
                if self.command == "GET" and re.match(r"^/repos/[^/]+/[^/]+/pulls/\d+/files\?", path):
                    return self._send(200, outer.pr_files if "page=1" in path else [])
                if self.command == "GET" and re.match(r"^/repos/[^/]+/[^/]+/pulls/\d+/reviews\?", path):
                    return self._send(200, outer.reviews if "page=1" in path else [])
                if self.command == "POST" and re.match(r"^/repos/[^/]+/[^/]+/pulls/\d+/reviews$", path):
                    outer._next_id += 1
                    review = {"id": outer._next_id, "user": {"type": "Bot"}, **body}
                    outer.reviews.append(review)
                    return self._send(200, {"id": outer._next_id})
                if self.command == "GET" and (m := re.match(r"^/repos/[^/]+/[^/]+/issues/(\d+)/comments\?per_page=(\d+)&page=(\d+)$", path)):
                    per, page = int(m.group(2)), int(m.group(3))
                    return self._send(200, outer.comments[(page - 1) * per: page * per])
                if self.command == "POST" and re.match(r"^/repos/[^/]+/[^/]+/issues/\d+/comments$", path):
                    outer._next_id += 1
                    comment = {"id": outer._next_id, "body": body["body"], "user": {"type": "Bot"}}
                    outer.comments.append(comment)
                    return self._send(201, comment)
                if self.command == "PATCH" and (m := re.match(r"^/repos/[^/]+/[^/]+/issues/comments/(\d+)$", path)):
                    for comment in outer.comments:
                        if comment["id"] == int(m.group(1)):
                            comment["body"] = body["body"]
                            return self._send(200, comment)
                    return self._send(404, {"message": "Not Found"})
                if self.command == "POST" and re.match(r"^/repos/[^/]+/[^/]+/check-runs$", path):
                    outer._next_id += 1
                    outer.check_runs.append({"id": outer._next_id, **body})
                    return self._send(201, {"id": outer._next_id})
                return self._send(404, {"message": "Not Found"})

            do_GET = do_POST = do_PATCH = do_PUT = _handle

        self.host = host
        self.server = HTTPServer((host, 0), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)

    @property
    def url(self) -> str:
        return f"http://{'127.0.0.1' if self.host in ('0.0.0.0', '') else self.host}:{self.server.server_port}"

    def __enter__(self):
        self.thread.start()
        return self

    def __exit__(self, *exc):
        self.server.shutdown()
        self.server.server_close()

    def add_foreign_comment(self, body: str, user_type: str = "User") -> dict:
        self._next_id += 1
        comment = {"id": self._next_id, "body": body, "user": {"type": user_type}}
        self.comments.append(comment)
        return comment


class FakeWebhook:
    def __init__(self):
        self.received: list[dict] = []
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_POST(self):
                outer.received.append(json.loads(self.rfile.read(int(self.headers["Content-Length"]))))
                self.send_response(200)
                self.end_headers()

        self.server = HTTPServer(("127.0.0.1", 0), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.server.server_port}/hook"

    def __enter__(self):
        self.thread.start()
        return self

    def __exit__(self, *exc):
        self.server.shutdown()
        self.server.server_close()


class FakeJira:
    """Máy chủ Jira REST v3 giả cho test E2E; chỉ ghi request trong bộ nhớ."""

    def __init__(self, host: str = "127.0.0.1"):
        self.issues: list[dict] = []
        self.requests: list[dict] = []
        self.forced_status: int | None = None
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_POST(self):
                length = int(self.headers.get("Content-Length") or 0)
                try:
                    body = json.loads(self.rfile.read(length))
                except ValueError:
                    body = {}
                outer.requests.append({"path": self.path, "body": body})
                if outer.forced_status:
                    return self._send(outer.forced_status, {"errorMessages": ["fake error"]})
                if self.path == "/rest/api/3/search/jql":
                    labels = set(re.findall(r'qcagent-[a-f0-9]{16,64}', str(body.get("jql") or "")))
                    found = [issue for issue in outer.issues if labels.intersection(issue["fields"].get("labels") or [])]
                    return self._send(200, {"issues": found, "isLast": True})
                if self.path == "/rest/api/3/issue":
                    item = {"key": f"QCSB-{len(outer.issues) + 1}", "fields": body.get("fields") or {}}
                    outer.issues.append(item)
                    return self._send(201, {"key": item["key"]})
                return self._send(404, {"errorMessages": ["not found"]})

            def _send(self, status, payload):
                raw = json.dumps(payload).encode("utf-8")
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)

        self.host = host
        self.server = HTTPServer((host, 0), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)

    @property
    def url(self):
        return f"http://{'127.0.0.1' if self.host in ('0.0.0.0', '') else self.host}:{self.server.server_port}"

    def __enter__(self):
        self.thread.start()
        return self

    def __exit__(self, *exc):
        self.server.shutdown()
        self.server.server_close()


class FakeAnthropic:
    """Máy chủ Messages API giả (`POST /v1/messages`) cho test tích hợp có LLM (S1-06; S1-08, S2, S4 dùng lại): CLI chạy qua `ANTHROPIC_BASE_URL=<url>`.

    `script` là danh sách kết quả cho từng lời gọi, phát lần lượt (hết thì lặp lại phần tử cuối):
      - dict      body JSON của response (vd nội dung `tests/fixtures/llm/*.json`) -> 200
      - Path      file JSON chứa body đó -> 200
      - int       mã lỗi (429 rate_limit_error, 529 overloaded_error, 500 api_error, ... ) với body lỗi đúng dạng của API
      - "timeout" im lặng `hang_s` giây rồi đóng kết nối (client có timeout ngắn sẽ báo timeout)
    Mỗi request được ghi vào `requests` ({"headers": {tên-thường: giá trị}, "body": JSON}); `count` là số lời gọi. Thiếu `x-api-key` (hoặc sai `key` nếu
    có đặt) hoặc `anthropic-version` thì trả 401 như API thật (vẫn được ghi, `rejected=True`).
    """
    ERRORS = {429: "rate_limit_error", 529: "overloaded_error", 500: "api_error", 400: "invalid_request_error", 401: "authentication_error"}

    def __init__(self, *script, key: str | None = None, hang_s: float = 5.0, host: str = "127.0.0.1"):
        self.script = list(script) or [{}]
        self.host = host
        self.key = key
        self.hang_s = hang_s
        self.requests: list[dict] = []
        self._lock = threading.Lock()
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def _send(self, status, payload):
                data = json.dumps(payload).encode("utf-8")
                try:
                    self.send_response(status)
                    self.send_header("Content-Type", "application/json")
                    self.send_header("Content-Length", str(len(data)))
                    self.end_headers()
                    self.wfile.write(data)
                except OSError:
                    pass

            def do_POST(self):
                length = int(self.headers.get("Content-Length") or 0)
                try:
                    body = json.loads(self.rfile.read(length)) if length else None
                except ValueError:
                    body = None
                headers = {name.lower(): value for name, value in self.headers.items()}
                with outer._lock:
                    index = len(outer.requests)
                    bad_auth = not headers.get("x-api-key") or not headers.get("anthropic-version") or (outer.key is not None and headers["x-api-key"] != outer.key)
                    outer.requests.append({"headers": headers, "body": body, "path": self.path, "rejected": bad_auth})
                    step = outer.script[min(index, len(outer.script) - 1)]
                if self.path != "/v1/messages":
                    return self._send(404, {"type": "error", "error": {"type": "not_found_error", "message": "no route"}})
                if bad_auth:
                    return self._send(401, {"type": "error", "error": {"type": "authentication_error", "message": "invalid x-api-key"}})
                if step == "timeout":
                    time.sleep(outer.hang_s)
                    self.close_connection = True
                    return
                if isinstance(step, int):
                    return self._send(step, {"type": "error", "error": {"type": outer.ERRORS.get(step, "api_error"), "message": "fake error"}})
                payload = json.loads(Path(step).read_text(encoding="utf-8")) if isinstance(step, Path) else step
                return self._send(200, payload)

        self.server = ThreadingHTTPServer((host, 0), Handler)
        self.server.daemon_threads = True
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)

    @property
    def url(self) -> str:
        return f"http://{'127.0.0.1' if self.host in ('0.0.0.0', '') else self.host}:{self.server.server_port}"

    @property
    def count(self) -> int:
        return len(self.requests)

    def __enter__(self):
        self.thread.start()
        return self

    def __exit__(self, *exc):
        self.server.shutdown()
        self.server.server_close()
