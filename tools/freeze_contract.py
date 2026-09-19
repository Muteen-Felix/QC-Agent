"""Đóng băng contract (RUN 5 2.4).  Hash được tính sau khi chuẩn hoá CRLF -> LF (Windows autocrlf).
    python tools/freeze_contract.py --write   # CHỈ chạy khi cả 3 người đồng ý đổi contract
    python tools/freeze_contract.py --check   # exit 1 nếu file contract lệch lock
"""
import hashlib, pathlib, sys

FILES = ["schemas/task_spec.json", "schemas/result.json", "workers/_template.yaml"]
LOCK = pathlib.Path("schemas/CONTRACT.sha256")


def digest(p):
    return hashlib.sha256(pathlib.Path(p).read_bytes().replace(b"\r\n", b"\n")).hexdigest()


def main(mode):
    now = {f: digest(f) for f in FILES}
    if mode == "--write":
        LOCK.write_text("".join(f"{h}  {f}\n" for f, h in now.items()), encoding="utf-8", newline="\n")
        print("ĐÃ GHI", LOCK)
        return 0
    locked = {}
    for line in LOCK.read_text(encoding="utf-8").splitlines():
        h, f = line.split("  ", 1)
        locked[f] = h
    bad = [f for f in FILES if locked.get(f) != now[f]]
    for f in bad:
        print("CONTRACT ĐÃ BỊ SỬA:", f)
    if not bad:
        print("CONTRACT NGUYÊN VẸN:", len(FILES), "file")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1] if len(sys.argv) > 1 else "--check"))
