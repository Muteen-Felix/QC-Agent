"""Đóng băng contract với SemVer.  Hash được tính sau khi chuẩn hoá CRLF -> LF (Windows autocrlf).
    python tools/freeze_contract.py --check                       # exit 1 nếu file contract lệch lock
    python tools/freeze_contract.py --write --version 1.1.0       # mở khoá: CHỈ sau khi đủ người duyệt (xem docs CI contract-check)
        [--base-ref origin/main]                                  # so với ref nào để suy ra mức bump tối thiểu (mặc định HEAD)
--write từ chối nếu: version không tăng so với lock, mức bump nhỏ hơn mức phân loại (minor/major), hoặc không có gì thay đổi.
"""
from __future__ import annotations

import hashlib
import json
import pathlib
import re
import subprocess
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import contract_diff  # noqa: E402

FILES = ["schemas/task_spec.json", "schemas/result.json", "workers/_template.yaml"]
LOCK_NAME = "schemas/CONTRACT.lock"
_SEMVER = re.compile(r"^(\d+)\.(\d+)\.(\d+)$")
_NEED = {contract_diff.NONE: 1, contract_diff.MINOR: 2, contract_diff.MAJOR: 3}  # patch=1 minor=2 major=3


def digest(path) -> str:
    return hashlib.sha256(pathlib.Path(path).read_bytes().replace(b"\r\n", b"\n")).hexdigest()


def parse_version(text: str) -> tuple[int, int, int]:
    m = _SEMVER.match(text.strip())
    if not m:
        raise ValueError(f"version phải có dạng MAJOR.MINOR.PATCH, nhận {text!r}")
    return tuple(int(x) for x in m.groups())


def bump_kind(old: str, new: str) -> int:
    """0 = không tăng (hoặc giảm); 1 = patch; 2 = minor; 3 = major."""
    o, n = parse_version(old), parse_version(new)
    if n <= o:
        return 0
    if n[0] > o[0]:
        return 3
    if n[1] > o[1]:
        return 2
    return 1


def read_lock(root: pathlib.Path) -> dict:
    return json.loads((root / LOCK_NAME).read_text(encoding="utf-8"))


def current_hashes(root: pathlib.Path) -> dict:
    return {f: digest(root / f) for f in FILES}


def write_lock(root: pathlib.Path, version: str, hashes: dict) -> None:
    text = json.dumps({"version": version, "files": hashes}, indent=2) + "\n"
    (root / LOCK_NAME).write_text(text, encoding="utf-8", newline="\n")


def git_show(root: pathlib.Path, ref: str, relpath: str) -> str | None:
    r = subprocess.run(["git", "-C", str(root), "show", f"{ref}:{relpath}"], capture_output=True)
    return r.stdout.decode("utf-8-sig").replace("\r\n", "\n") if r.returncode == 0 else None


def required_level(root: pathlib.Path, base_ref: str) -> tuple[str, list[str]]:
    """Mức thay đổi lớn nhất của các file contract giữa base_ref và working tree."""
    level, reasons = contract_diff.NONE, []
    for f in FILES:
        new = (root / f).read_bytes().decode("utf-8-sig").replace("\r\n", "\n") if (root / f).is_file() else None
        lv, why = contract_diff.classify_file(f, git_show(root, base_ref, f), new)
        level = lv if contract_diff._RANK[lv] > contract_diff._RANK[level] else level
        reasons += why
    return level, reasons


def cmd_check(root: pathlib.Path) -> int:
    lock, now = read_lock(root), current_hashes(root)
    bad = [f for f in FILES if lock["files"].get(f) != now[f]]
    for f in bad:
        print("CONTRACT ĐÃ BỊ SỬA:", f)
    if not bad:
        print(f"CONTRACT NGUYÊN VẸN: {len(FILES)} file (v{lock['version']})")
    return 1 if bad else 0


def cmd_write(root: pathlib.Path, version: str, base_ref: str) -> int:
    lock, now = read_lock(root), current_hashes(root)
    if now == lock["files"]:
        print("KHÔNG CÓ GÌ ĐỂ ĐÓNG BĂNG: file contract trùng lock.")
        return 1
    got = bump_kind(lock["version"], version)
    if got == 0:
        print(f"TỪ CHỐI: version {version} không lớn hơn version hiện tại {lock['version']}.")
        return 1
    level, reasons = required_level(root, base_ref)
    if got < _NEED[level]:
        names = {1: "patch", 2: "minor", 3: "major"}
        print(f"TỪ CHỐI: thay đổi mức {level.upper()} cần bump {names[_NEED[level]]}, bạn chỉ bump {names[got]}.")
        for line in reasons:
            print(" ", line)
        return 1
    write_lock(root, version, now)
    print(f"ĐÃ GHI {LOCK_NAME} v{version} (mức thay đổi: {level.upper()})")
    return 0


def main(argv: list[str], root: pathlib.Path | None = None) -> int:
    if sys.stdout and hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    root = root or pathlib.Path(__file__).resolve().parent.parent
    mode = argv[0] if argv else "--check"
    if mode == "--check":
        return cmd_check(root)
    if mode == "--write":
        opts = dict(zip(argv[1::2], argv[2::2]))
        if "--version" not in opts:
            print("--write cần --version X.Y.Z", file=sys.stderr)
            return 2
        try:
            return cmd_write(root, opts["--version"], opts.get("--base-ref", "HEAD"))
        except ValueError as error:
            print(f"TỪ CHỐI: {error}")
            return 1
    print(__doc__, file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
