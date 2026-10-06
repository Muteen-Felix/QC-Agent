"""Cache file dùng chung cho Select và GT một-lời-gọi (S4-02). Mỗi entry là một file `<khoá>.json` trong một thư mục phẳng.

Ranh giới cứng:
  - Cache chỉ là tối ưu: MỌI lỗi đọc/ghi (file hỏng, quá lớn, đĩa đầy, quyền) đều thành "miss" hoặc "không ghi", không bao giờ làm hỏng lệnh gọi.
  - Khoá là sha256 hex nên tên file không thể thoát khỏi thư mục cache. Nội dung entry là dữ liệu KHÔNG TIN CẬY (cache có thể bị sửa tay hoặc đầu độc):
    caller PHẢI validate lại entry theo schema hiện tại trước khi dùng (xem selector/cache.py, groundtruth/cache.py).
  - Ghi nguyên tử (file tạm cùng thư mục rồi `os.replace`) để hai tiến trình không để lại entry ghi dở.
  - Không log nội dung entry; chỉ caller log hit/miss kèm 8 ký tự đầu của khoá.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
from pathlib import Path

MAX_ENTRY_BYTES = 2_000_000   # entry GT (tới 16k token đầu ra) cỡ vài trăm KB; lớn hơn thế coi là hỏng/độc
_KEY = re.compile(r"[0-9a-f]{64}")


def digest(value) -> str:
    """sha256 của JSON chuẩn hoá (khoá sắp xếp, UTF-8): cùng nội dung logic ra cùng hash."""
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")).hexdigest()


def make_key(*parts: str) -> str:
    """Khoá từ danh sách chuỗi. Mã hoá thành mảng JSON thay vì nối chuỗi để hai trường liền nhau không "dính" thành cùng một khoá."""
    return digest(list(parts))


def read(directory: Path, key: str) -> dict | None:
    """Entry (dict) hoặc None khi không có / hỏng / quá lớn. Chưa được tin: caller validate."""
    if not _KEY.fullmatch(key):
        return None
    path = Path(directory) / f"{key}.json"
    try:
        if path.stat().st_size > MAX_ENTRY_BYTES:
            return None
        entry = json.loads(path.read_text(encoding="utf-8"))
        os.utime(path)   # entry vừa dùng thì "mới": prune giữ lại
    except (OSError, ValueError):
        return None
    return entry if isinstance(entry, dict) else None


def write(directory: Path, key: str, entry: dict) -> bool:
    """Ghi nguyên tử. True nếu đã ghi; False nếu bất kỳ lỗi nào (không ném)."""
    if not _KEY.fullmatch(key):
        return False
    directory = Path(directory)
    tmp = directory / f".{key}.{os.getpid()}.tmp"
    try:
        directory.mkdir(parents=True, exist_ok=True)
        tmp.write_bytes(json.dumps(entry, ensure_ascii=False, sort_keys=True).encode("utf-8"))
        os.replace(tmp, directory / f"{key}.json")
        return True
    except (OSError, ValueError, TypeError):
        try:
            tmp.unlink()
        except OSError:
            pass
        return False


def prune(directory: Path, keep: int) -> None:
    """Giữ `keep` entry mới nhất (theo mtime), xoá phần còn lại. Best-effort: thư mục phình dần qua chuỗi restore-keys của Actions."""
    try:
        files = sorted((p for p in Path(directory).glob("*.json") if _KEY.fullmatch(p.stem)), key=lambda p: p.stat().st_mtime, reverse=True)
    except OSError:
        return
    for path in files[keep:]:
        try:
            path.unlink()
        except OSError:
            pass
