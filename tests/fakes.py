"""Máy chủ HTTP giả cho test tích hợp: GitHub API (check-runs, comment PR) và webhook. Ghi lại mọi request để kiểm chứng."""
import json
import re
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer


class FakeGitHub:
    def __init__(self, host: str = "127.0.0.1"):
        self.comments: list[dict] = []
        self.check_runs: list[dict] = []
        self.requests: list[dict] = []
        self.policy_files: dict[str, str] = {}   # configs/projects/<tên> của "qc-agent@main" mà contents API giả trả về
        self.main_sha = "a" * 40
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
                if self.command == "GET" and re.match(r"^/repos/[^/]+/[^/]+/commits/main$", path):
                    return self._send_raw(200, outer.main_sha)
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

            do_GET = do_POST = do_PATCH = _handle

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
