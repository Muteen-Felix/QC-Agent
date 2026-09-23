"""Phân loại thay đổi contract theo SemVer. Quy tắc: KHÔNG CHẮC => MAJOR.

  NONE  : không đổi ngữ nghĩa (chỉ mô tả/comment/format)      -> bump PATCH
  MINOR : chỉ THÊM property tuỳ chọn / định nghĩa mới          -> bump MINOR (1 người duyệt)
  MAJOR : xoá/đổi/thu hẹp bất cứ thứ gì, đổi `required`, `enum`, `type`, ràng buộc... -> bump MAJOR (3 người, có Lead/Core)

    python tools/contract_diff.py OLD NEW      # in mức + lý do, exit 0
"""
from __future__ import annotations

import json
import sys

import yaml

NONE, MINOR, MAJOR = "none", "minor", "major"
_RANK = {NONE: 0, MINOR: 1, MAJOR: 2}
_DOC_KEYS = {"description", "title", "$comment", "examples"}  # không ảnh hưởng validate
_MAP_KEYS = {"properties", "$defs", "definitions"}  # {tên: schema}: thêm = MINOR, xoá = MAJOR


def _up(level: str, other: str) -> str:
    return level if _RANK[level] >= _RANK[other] else other


class _Acc:
    def __init__(self):
        self.level, self.reasons = NONE, []

    def add(self, level: str, reason: str):
        self.level = _up(self.level, level)
        if level != NONE:
            self.reasons.append(f"{level.upper()}: {reason}")


def _walk_schema(old, new, path: str, acc: _Acc) -> None:
    if old == new:
        return
    if not (isinstance(old, dict) and isinstance(new, dict)):
        acc.add(MAJOR, f"{path or '<root>'} đổi giá trị")
        return
    for key in sorted(set(old) | set(new)):
        here = f"{path}/{key}" if path else key
        if key in _DOC_KEYS:
            continue
        if old.get(key) == new.get(key) and key in old and key in new:
            continue
        if key in _MAP_KEYS:
            o, n = old.get(key, {}), new.get(key, {})
            if not (isinstance(o, dict) and isinstance(n, dict)):
                acc.add(MAJOR, f"{here} không phải object")
                continue
            for name in sorted(set(o) | set(n)):
                sub = f"{here}/{name}"
                if name not in n:
                    acc.add(MAJOR, f"xoá {sub}")
                elif name not in o:
                    acc.add(MINOR, f"thêm {sub}")
                else:
                    _walk_schema(o[name], n[name], sub, acc)
        elif key == "items" and isinstance(old.get(key), dict) and isinstance(new.get(key), dict):
            _walk_schema(old[key], new[key], here, acc)
        elif key not in old:
            acc.add(MAJOR, f"thêm ràng buộc/từ khoá {here}")  # thêm required/minimum/pattern... là thu hẹp
        elif key not in new:
            acc.add(MAJOR, f"xoá {here}")
        else:
            acc.add(MAJOR, f"đổi {here}")  # required, enum, type, const, pattern, allOf, additionalProperties...


def classify_schema(old: dict, new: dict) -> tuple[str, list[str]]:
    acc = _Acc()
    _walk_schema(old, new, "", acc)
    return acc.level, acc.reasons


def _walk_yaml(old, new, path: str, acc: _Acc) -> None:
    if old == new:
        return
    if isinstance(old, dict) and isinstance(new, dict):
        for key in sorted(set(old) | set(new), key=str):
            here = f"{path}.{key}" if path else str(key)
            if key not in new:
                acc.add(MAJOR, f"xoá khoá {here}")
            elif key not in old:
                acc.add(MINOR, f"thêm khoá {here}")
            else:
                _walk_yaml(old[key], new[key], here, acc)
        return
    acc.add(MAJOR, f"đổi giá trị {path or '<root>'}")


def classify_yaml(old_text: str, new_text: str) -> tuple[str, list[str]]:
    acc = _Acc()
    _walk_yaml(yaml.safe_load(old_text), yaml.safe_load(new_text), "", acc)
    return acc.level, acc.reasons


def classify_file(path: str, old_text: str | None, new_text: str | None) -> tuple[str, list[str]]:
    """old/new = None nghĩa là file chưa tồn tại / đã bị xoá."""
    if old_text == new_text:
        return NONE, []
    if old_text is None:
        return MINOR, [f"MINOR: thêm file contract {path}"]
    if new_text is None:
        return MAJOR, [f"MAJOR: xoá file contract {path}"]
    try:
        if path.endswith(".json"):
            level, reasons = classify_schema(json.loads(old_text), json.loads(new_text))
        elif path.endswith((".yaml", ".yml")):
            level, reasons = classify_yaml(old_text, new_text)
        else:
            return MAJOR, [f"MAJOR: không phân loại được định dạng {path}"]
    except (ValueError, yaml.YAMLError) as error:
        return MAJOR, [f"MAJOR: không parse được {path}: {error}"]
    return level, reasons


def main(argv: list[str]) -> int:
    if sys.stdout and hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    if len(argv) != 2:
        print(__doc__, file=sys.stderr)
        return 2
    read = lambda p: open(p, encoding="utf-8-sig").read()  # noqa: E731
    level, reasons = classify_file(argv[1], read(argv[0]), read(argv[1]))
    print(level.upper())
    for line in reasons:
        print(" ", line)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
