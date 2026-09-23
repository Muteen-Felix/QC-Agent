"""Băm sha256 và dựng evidence[] đúng contract: [{"kind", "uri", "sha256"}]."""
import hashlib
from pathlib import Path


def sha256_file(path) -> str:
    """SHA-256 của byte thô của file (không chuẩn hoá xuống dòng/encoding)."""
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def collect(items, base=None) -> list[dict]:
    """items = [(kind, Path)] -> [{"kind", "uri", "sha256"}].

    uri là đường dẫn posix tương đối so với `base` (mặc định: cwd lúc gọi). Path tương đối được hiểu
    là tương đối so với `base`. File không tồn tại -> FileNotFoundError (adapter bắt và đổi thành `error`).
    """
    base = Path.cwd() if base is None else Path(base)
    out = []
    for kind, path in items:
        p = Path(path)
        if not p.is_absolute():
            p = base / p
        if not p.is_file():
            raise FileNotFoundError(f"evidence {kind!r} không tồn tại: {p}")
        uri = p.resolve().relative_to(base.resolve()).as_posix()
        out.append({"kind": kind, "uri": uri, "sha256": sha256_file(p)})
    return out


def missing_kinds(evidence, required) -> list[str]:
    """Các kind trong `required` chưa có mặt trong `evidence` (giữ thứ tự, không trùng)."""
    have = {e["kind"] for e in evidence}
    return [k for k in dict.fromkeys(required) if k not in have]
