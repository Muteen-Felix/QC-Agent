"""SUT mẫu CẦN database (S4-09), đọc DATABASE_URL từ env. Giống `lifespan` của VAHAN: kết nối ngay lúc khởi động và THOÁT nếu không được
(nên container không bao giờ trả lời HTTP, bước "Start SUT" đỏ). /health trả 200 chỉ khi còn kết nối được DB, 503 nếu không.
Nói giao thức PostgreSQL bằng thư viện chuẩn (xác thực mật khẩu dạng văn bản; DB của test đặt POSTGRES_HOST_AUTH_METHOD=password): image không cần
pip/mạng, và mật khẩu SAI bị DB từ chối nên bí mật đi qua workflow được kiểm thật. Chỉ in tên kiểu lỗi, không in thông điệp (có thể kèm host/tài khoản)."""
import json
import os
import socket
import struct
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import unquote, urlsplit


def _read(sock: socket.socket, size: int) -> bytes:
    data = b""
    while len(data) < size:
        chunk = sock.recv(size - len(data))
        if not chunk:
            raise ConnectionError("đóng kết nối")
        data += chunk
    return data


def _message(sock: socket.socket) -> tuple[bytes, bytes]:
    kind = _read(sock, 1)
    (length,) = struct.unpack("!I", _read(sock, 4))
    return kind, _read(sock, length - 4)


def connect() -> socket.socket:
    url = urlsplit(os.environ["DATABASE_URL"])
    user, password, database = unquote(url.username or ""), unquote(url.password or ""), url.path.lstrip("/")
    sock = socket.create_connection((url.hostname, url.port or 5432), timeout=3)
    try:
        params = f"user\0{user}\0database\0{database}\0\0".encode()
        sock.sendall(struct.pack("!II", 8 + len(params), 196608) + params)
        while True:
            kind, body = _message(sock)
            if kind == b"R":
                (code,) = struct.unpack("!I", body[:4])
                if code == 3:   # AuthenticationCleartextPassword
                    secret = password.encode() + b"\0"
                    sock.sendall(b"p" + struct.pack("!I", 4 + len(secret)) + secret)
                elif code != 0:
                    raise PermissionError(f"kiểu xác thực {code}")
            elif kind == b"E":
                raise PermissionError("DB từ chối")
            elif kind == b"Z":
                return sock
    except BaseException:
        sock.close()
        raise


def db_ok() -> bool:
    try:
        sock = connect()
        try:
            query = b"SELECT 1\0"
            sock.sendall(b"Q" + struct.pack("!I", 4 + len(query)) + query)
            rows = []
            while True:
                kind, body = _message(sock)
                if kind == b"D":
                    rows.append(body)
                elif kind == b"E":
                    return False
                elif kind == b"Z":
                    return len(rows) == 1 and rows[0].endswith(b"1")
        finally:
            sock.close()
    except Exception:
        return False


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        ok = db_ok()
        body = json.dumps({"db": "ok" if ok else "down"}).encode()
        self.send_response(200 if ok else 503)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass


if __name__ == "__main__":
    try:
        connect().close()
    except KeyError:
        sys.exit("thiếu DATABASE_URL")
    except Exception as error:
        sys.exit(f"không kết nối được DB ({type(error).__name__})")
    ThreadingHTTPServer(("0.0.0.0", 8000), Handler).serve_forever()
