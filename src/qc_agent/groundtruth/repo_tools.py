"""Công cụ ĐỌC (chỉ đọc) trên repo SUT và OpenAPI đầy đủ cho Ground-Truth agent: `list_dir`, `read_file`, `grep` và `operation`, `schema`.

Mã nguồn và OpenAPI là dữ liệu KHÔNG TIN CẬY (comment trong mã có thể chứa lời chỉ thị). Mỗi kết quả:
  - nằm trong thẻ (`<file>`, `<listing>`, `<matches>`, `<openapi>`) và mọi thẻ trùng tên trong nội dung bị vô hiệu hoá, nên nội dung không thoát ra khỏi vùng phân cách;
  - đã qua redact bí mật theo mẫu (khoá API, token, private key, mật khẩu trong chuỗi kết nối...). Redact chỉ là lớp phụ: lớp chính là deny-list và việc chạy
    trong container với repo mount `:ro`;
  - mang `categories` để vòng lặp ghi egress đúng loại dữ liệu rời máy (`source_code`, `api_spec`).

Sandbox (an toàn theo thiết kế, không tin model):
  - path tuyệt đối, ổ đĩa, UNC, đoạn `..`, NUL đều bị từ chối; path được `resolve(strict=True)` rồi phải nằm TRONG root (symlink/junction trỏ ra ngoài bị chặn);
  - deny-list áp lên path theo chữ VÀ theo path đã resolve (symlink trỏ tới `.env` vẫn bị chặn), không phân biệt hoa thường; mục bị chặn biến mất khỏi `list_dir`/`grep`;
  - giới hạn: kích thước file, tổng byte phục vụ, số mục, số kết quả grep, độ dài dòng, thời gian grep. Hết ngân sách đọc thì tool trả lỗi, không im lặng cắt.
Không có lệnh ghi, không có shell, không có mạng.

Log/thông điệp lỗi chỉ có số đếm: không bao giờ có đường dẫn hay nội dung (xem logging_setup). `served` là tập path mà model đã THẤY nội dung, để kiểm `evidence` của TC.
"""
from __future__ import annotations

import json
import os
import re
import time
from pathlib import Path

from qc_agent.llm.agent_loop import ToolOutcome
from qc_agent.scaffold import openapi

DEFAULT_DENY = (
    ".git/**", ".qc-agent/**", "**/node_modules/**", "**/.venv/**", "**/venv/**", "**/__pycache__/**", "**/.tox/**", "**/.mypy_cache/**", "**/.pytest_cache/**",
    "dist/**", "build/**", "**/.env", "**/.env.*", "**/*.pem", "**/*.key", "**/*.p12", "**/*.pfx", "**/*.jks", "**/*.keystore", "**/id_rsa*", "**/id_ed25519*",
    "**/.ssh/**", "**/.aws/**", "**/*secret*", "**/*secret*/**", "**/*credential*", "**/*credential*/**", "**/.npmrc", "**/.pypirc", "**/.netrc", "**/*.tfstate*", "**/*.tfvars",
    "**/*.lock", "**/package-lock.json", "**/pnpm-lock.yaml", "**/yarn.lock", "**/*.min.js", "**/*.map", "**/*.sqlite*", "**/*.db",
)
MAX_FILE_BYTES = 256_000
MAX_TOTAL_BYTES = 3_000_000
MAX_LIST_ENTRIES = 400
MAX_LIST_DEPTH = 3
MAX_READ_LINES = 400
MAX_GREP_HITS = 100
MAX_GREP_FILE_BYTES = 1_000_000
MAX_GREP_SECONDS = 10.0
MAX_PATTERN = 200
MAX_LINE_CHARS = 400
MAX_PATH = 300
OPENAPI_MAX_BYTES = 60_000
OPENAPI_MAX_DEPTH = 6
OPENAPI_MAX_STRING = 500
REDACTED = "«REDACTED»"
_BINARY_SNIFF = 8192
_TAG = re.compile(r"<(?=/?\s*(?:file|listing|matches|openapi|prd|endpoints|repo_overview|repo_map|existing_cases|validation_error|tool_output)\b)", re.I)

_SECRET_PATTERNS = (
    re.compile(r"AKIA[0-9A-Z]{16}"),
    re.compile(r"\bgh[pousr]_[A-Za-z0-9]{36,}"), re.compile(r"\bgithub_pat_[A-Za-z0-9_]{50,}"),
    re.compile(r"\bsk-ant-[A-Za-z0-9_-]{20,}"), re.compile(r"\bsk-[A-Za-z0-9]{32,}"),
    re.compile(r"\bAIza[0-9A-Za-z_-]{35}"), re.compile(r"\bxox[abprs]-[A-Za-z0-9-]{10,}"),
    re.compile(r"\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}"),
)
_CREDENTIAL_ASSIGN = re.compile(r"(?i)((?:password|passwd|secret|token|api[_-]?key|authorization|bearer)['\"]?\s*[:=]\s*)(['\"])[^'\"\n]{8,}\2")
_URL_CREDENTIALS = re.compile(r"(://)[^/\s:@'\"]+:[^/\s@'\"]+@")
_PRIVATE_KEY_BEGIN = re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----")
_PRIVATE_KEY_END = re.compile(r"-----END [A-Z ]*PRIVATE KEY-----")


def fence(text: str) -> str:
    """Vô hiệu hoá thẻ mở/đóng của vùng phân cách bên trong dữ liệu: `</file>` thành `&lt;/file>`."""
    return _TAG.sub("&lt;", text)


def _attrs(attrs: dict) -> str:
    """Thuộc tính của thẻ bao: tên file Linux có thể chứa `"` hay `<`, nên thoát trước khi nhúng."""
    def clean(value) -> str:
        return str(value).replace("&", "&amp;").replace('"', "&quot;").replace("<", "&lt;").replace(">", "&gt;")
    return " ".join(f'{key}="{clean(value)}"' for key, value in attrs.items())


def redact(text: str) -> str:
    """Che bí mật nhận ra được trong MỘT dòng. Không bao giờ ném lỗi, không đổi số dòng."""
    for pattern in _SECRET_PATTERNS:
        text = pattern.sub(REDACTED, text)
    text = _CREDENTIAL_ASSIGN.sub(lambda m: f"{m.group(1)}{m.group(2)}{REDACTED}{m.group(2)}", text)
    return _URL_CREDENTIALS.sub(lambda m: f"{m.group(1)}{REDACTED}@", text)


def _redact_lines(lines: list[str]) -> list[str]:
    out, inside_key = [], False
    for line in lines:
        if inside_key:
            out.append(REDACTED)
            inside_key = not _PRIVATE_KEY_END.search(line)
        elif _PRIVATE_KEY_BEGIN.search(line):
            out.append(REDACTED)
            inside_key = not _PRIVATE_KEY_END.search(line)
        else:
            out.append(redact(line))
    return out


def _glob_regex(pattern: str) -> re.Pattern:
    """Glob kiểu `**` (khớp nhiều cấp thư mục) -> regex, không phân biệt hoa thường. `*` và `?` không vượt qua `/`."""
    out, i = [], 0
    while i < len(pattern):
        if pattern.startswith("**/", i):
            out.append("(?:.*/)?")
            i += 3
        elif pattern.startswith("**", i):
            out.append(".*")
            i += 2
        elif pattern[i] == "*":
            out.append("[^/]*")
            i += 1
        elif pattern[i] == "?":
            out.append("[^/]")
            i += 1
        else:
            out.append(re.escape(pattern[i]))
            i += 1
    return re.compile("".join(out) + r"\Z", re.I)


class SandboxError(ValueError):
    """Lý do ngắn, an toàn để trả cho model (không chứa nội dung file)."""


class RepoSandbox:
    def __init__(self, root: Path, *, extra_deny: tuple[str, ...] = (), max_file_bytes: int = MAX_FILE_BYTES, max_total_bytes: int = MAX_TOTAL_BYTES,
                 max_list_entries: int = MAX_LIST_ENTRIES, max_grep_hits: int = MAX_GREP_HITS):
        self.root = Path(root).resolve(strict=True)
        if not self.root.is_dir():
            raise ValueError("root không phải thư mục")
        self._deny = [_glob_regex(p) for p in (*DEFAULT_DENY, *extra_deny)]
        self.max_file_bytes, self.max_total_bytes, self.max_list_entries, self.max_grep_hits = max_file_bytes, max_total_bytes, max_list_entries, max_grep_hits
        self.bytes_served = 0
        self.served: set[str] = set()     # path (POSIX, tương đối root) mà model đã thấy nội dung
        self.denied_hits = 0              # số lần model đụng path bị chặn (chỉ đếm)

    # ---------------- path ----------------

    def denied(self, rel: str, *, is_dir: bool) -> bool:
        """`rel` POSIX, tương đối root. Chặn nếu chính nó hoặc BẤT KỲ thư mục tổ tiên nào khớp deny-list."""
        parts = rel.split("/")
        candidates = ["/".join(parts[:i]) + "/" for i in range(1, len(parts))] + [rel + "/" if is_dir else rel]
        return any(pattern.match(c) for pattern in self._deny for c in candidates)

    def _lexical(self, raw) -> str:
        if not isinstance(raw, str) or not raw or len(raw) > MAX_PATH or "\x00" in raw:
            raise SandboxError("path không hợp lệ")
        norm = raw.replace("\\", "/")
        if norm.startswith(("/", "~")) or re.match(r"[A-Za-z]:", norm):
            raise SandboxError("path phải là tương đối so với gốc repo")
        parts = [p for p in norm.split("/") if p not in ("", ".")]
        if any(p == ".." for p in parts):
            raise SandboxError("path không được chứa '..'")
        return "/".join(parts)

    def resolve(self, raw) -> tuple[Path, str]:
        """(path đã resolve, path tương đối POSIX). Ném SandboxError nếu vi phạm."""
        lexical = self._lexical(raw)
        if lexical and self.denied(lexical, is_dir=False):
            self.denied_hits += 1
            raise SandboxError("path bị chặn")
        try:
            target = (self.root / lexical).resolve(strict=True) if lexical else self.root
        except (FileNotFoundError, NotADirectoryError):
            raise SandboxError("không tìm thấy path") from None
        except (OSError, RuntimeError, ValueError):   # vòng symlink, tên không hợp lệ trên hệ điều hành
            raise SandboxError("path không đọc được") from None
        try:
            rel = target.relative_to(self.root).as_posix()
        except ValueError:
            self.denied_hits += 1
            raise SandboxError("path nằm ngoài repo") from None   # symlink/junction trỏ ra ngoài
        rel = "" if rel == "." else rel
        if rel and self.denied(rel, is_dir=target.is_dir()):
            self.denied_hits += 1
            raise SandboxError("path bị chặn")                     # symlink trỏ vào file bị chặn
        return target, rel

    def _inside(self, path) -> bool:
        """Đường dẫn THẬT (đã theo symlink/junction) còn nằm trong root không. `is_symlink()` KHÔNG thấy junction của Windows (Python 3.11), nên mọi
        thư mục khi duyệt cây đều phải qua kiểm tra này; test junction đã bắt được đúng lỗ hổng đó ở grep."""
        try:
            Path(os.path.realpath(path)).relative_to(self.root)
            return True
        except (ValueError, OSError):
            return False

    def _is_binary(self, head: bytes) -> bool:
        return b"\x00" in head

    def _charge(self, text: str) -> None:
        size = len(text.encode("utf-8"))
        if self.bytes_served + size > self.max_total_bytes:
            raise SandboxError("hết ngân sách đọc: dừng đọc thêm và dùng những gì đã có")
        self.bytes_served += size

    def _wrap(self, tag: str, attrs: dict, body: str, categories=frozenset({"source_code"})) -> ToolOutcome:
        return ToolOutcome(f"<{tag} {_attrs(attrs)}>\n{fence(body)}\n</{tag}>", categories=frozenset(categories))

    # ---------------- tools ----------------

    def list_dir(self, path: str = ".", depth: int = 1) -> ToolOutcome:
        try:
            target, rel = self.resolve(path if path not in ("", None) else ".")
            if not target.is_dir():
                raise SandboxError("không phải thư mục")
            depth = min(max(int(depth), 1), MAX_LIST_DEPTH)
            lines, truncated = self._tree(target, rel, depth)
            body = "\n".join(lines) if lines else "(trống)"
            self._charge(body)
        except SandboxError as error:
            return ToolOutcome(str(error), is_error=True)
        return self._wrap("listing", {"path": rel or ".", "truncated": str(truncated).lower()}, body)

    def _tree(self, directory: Path, rel: str, depth: int) -> tuple[list[str], bool]:
        lines: list[str] = []

        def walk(current: Path, current_rel: str, level: int) -> bool:
            try:
                with os.scandir(current) as scan:
                    entries = sorted(scan, key=lambda e: (not e.is_dir(follow_symlinks=False), e.name.lower()))
            except OSError:
                return False
            for entry in entries:
                entry_rel = f"{current_rel}/{entry.name}" if current_rel else entry.name
                is_dir = entry.is_dir(follow_symlinks=False)
                if entry.is_symlink() or self.denied(entry_rel, is_dir=is_dir) or (is_dir and not self._inside(entry.path)):
                    continue   # symlink/junction không được dẫn theo (tránh thoát root); mục bị chặn biến mất
                if len(lines) >= self.max_list_entries:
                    return True
                indent = "  " * (level - 1)
                if is_dir:
                    lines.append(f"{indent}{entry.name}/")
                    if level < depth and walk(Path(entry.path), entry_rel, level + 1):
                        return True
                else:
                    try:
                        size = entry.stat(follow_symlinks=False).st_size
                    except OSError:
                        size = 0
                    lines.append(f"{indent}{entry.name} ({size} B)")
            return False

        return lines, walk(directory, rel, 1)

    def overview(self, depth: int = 2, max_entries: int = 200) -> str:
        """Cây thư mục do CODE dựng cho user message đầu (không tính vào ngân sách đọc, không đánh dấu `served`). Đã redact + fence ở caller."""
        saved = self.max_list_entries
        self.max_list_entries = max_entries
        try:
            lines, truncated = self._tree(self.root, "", min(max(depth, 1), MAX_LIST_DEPTH))
        finally:
            self.max_list_entries = saved
        return fence("\n".join(lines) + ("\n… (cắt bớt)" if truncated else ""))

    def read_file(self, path: str, start_line: int = 1, max_lines: int = MAX_READ_LINES) -> ToolOutcome:
        try:
            target, rel = self.resolve(path)
            if not target.is_file():
                raise SandboxError("không phải file")
            size = target.stat().st_size
            if size > self.max_file_bytes:
                raise SandboxError(f"file quá lớn ({size} B > {self.max_file_bytes} B): dùng grep để tìm đoạn cần đọc")
            raw = target.read_bytes()
            if self._is_binary(raw[:_BINARY_SNIFF]):
                raise SandboxError("file nhị phân")
            try:
                text = raw.decode("utf-8-sig")
            except UnicodeDecodeError:
                raise SandboxError("file không phải UTF-8") from None
            lines = text.splitlines()
            start = max(int(start_line), 1)
            count = min(max(int(max_lines), 1), MAX_READ_LINES)
            chunk = _redact_lines([line[:MAX_LINE_CHARS] + ("…" if len(line) > MAX_LINE_CHARS else "") for line in lines[start - 1:start - 1 + count]])
            end = start - 1 + len(chunk)
            body = "\n".join(f"{start + i}\t{line}" for i, line in enumerate(chunk)) if chunk else "(không có dòng nào trong khoảng này)"
            self._charge(body)
        except SandboxError as error:
            return ToolOutcome(str(error), is_error=True)
        except (OSError, ValueError, TypeError):
            return ToolOutcome("không đọc được file", is_error=True)
        self.served.add(rel)
        return self._wrap("file", {"path": rel, "lines": f"{start}-{end}", "total": len(lines), "truncated": str(end < len(lines)).lower()}, body)

    def grep(self, pattern: str, path_glob: str | None = None, fixed: bool = True) -> ToolOutcome:
        try:
            if not isinstance(pattern, str) or not pattern or len(pattern) > MAX_PATTERN:
                raise SandboxError(f"pattern phải dài 1..{MAX_PATTERN} ký tự")
            try:
                regex = re.compile(re.escape(pattern) if fixed else pattern)
            except re.error:
                raise SandboxError("pattern không phải regex hợp lệ") from None
            glob = None
            if path_glob:
                if not isinstance(path_glob, str) or len(path_glob) > MAX_PATH or ".." in path_glob.replace("\\", "/").split("/") or path_glob.startswith(("/", "~")):
                    raise SandboxError("path_glob không hợp lệ")
                glob = _glob_regex(path_glob.replace("\\", "/"))
            hits, seen, deadline = [], set(), time.monotonic() + MAX_GREP_SECONDS
            capped = self._scan(regex, glob, hits, seen, deadline)
            body = "\n".join(hits) if hits else "(không có kết quả)"
            self._charge(body)
        except SandboxError as error:
            return ToolOutcome(str(error), is_error=True)
        self.served.update(seen)
        return self._wrap("matches", {"count": len(hits), "truncated": str(capped).lower()}, body)

    def iter_files(self, suffixes: tuple[str, ...] = (".py",), *, max_files: int = 400, max_bytes: int = 200_000, skip_tests: bool = True) -> list[tuple[str, Path]]:
        """[(path tương đối POSIX, Path)] đã xếp, cho bộ quét tất định (repo_map.py): cùng deny-list, cùng luật không-đi-theo-symlink/junction, nằm trong root.
        Không đánh dấu `served` và không tính ngân sách đọc: nội dung không đi tới LLM ở đây mà chỉ đi qua bộ quét của code."""
        found: list[tuple[str, Path]] = []
        test_dirs = {"tests", "test", "__tests__", "e2e", "spec", "specs"}
        for current, dirs, files in os.walk(self.root, followlinks=False):
            here = Path(current)
            rel_dir = here.relative_to(self.root).as_posix()
            rel_dir = "" if rel_dir == "." else rel_dir
            dirs[:] = sorted(d for d in dirs if not self.denied(f"{rel_dir}/{d}" if rel_dir else d, is_dir=True) and not (here / d).is_symlink() and self._inside(here / d)
                             and not (skip_tests and d.lower() in test_dirs))
            for name in sorted(files):
                rel = f"{rel_dir}/{name}" if rel_dir else name
                path = here / name
                lowered = name.lower()
                if not lowered.endswith(suffixes) or path.is_symlink() or self.denied(rel, is_dir=False):
                    continue
                if skip_tests and (lowered.startswith("test_") or lowered.endswith(("_test.py", ".test.js", ".test.ts", ".spec.js", ".spec.ts")) or lowered == "conftest.py"):
                    continue
                try:
                    if path.stat().st_size > max_bytes:
                        continue
                except OSError:
                    continue
                found.append((rel, path))
                if len(found) >= max_files:
                    return found
        return found

    def _scan(self, regex: re.Pattern, glob, hits: list[str], seen: set[str], deadline: float) -> bool:
        for current, dirs, files in os.walk(self.root, followlinks=False):
            here = Path(current)
            rel_dir = here.relative_to(self.root).as_posix()
            rel_dir = "" if rel_dir == "." else rel_dir
            dirs[:] = sorted(d for d in dirs if not self.denied(f"{rel_dir}/{d}" if rel_dir else d, is_dir=True)
                             and not (here / d).is_symlink() and self._inside(here / d))
            for name in sorted(files):
                if time.monotonic() > deadline:
                    return True
                rel = f"{rel_dir}/{name}" if rel_dir else name
                path = here / name
                if path.is_symlink() or self.denied(rel, is_dir=False) or (glob is not None and not glob.match(rel)):
                    continue
                try:
                    if path.stat().st_size > MAX_GREP_FILE_BYTES:
                        continue
                    raw = path.read_bytes()
                except OSError:
                    continue
                if self._is_binary(raw[:_BINARY_SNIFF]):
                    continue
                try:
                    lines = raw.decode("utf-8-sig").splitlines()
                except UnicodeDecodeError:
                    continue
                for number, line in enumerate(lines, 1):
                    clipped = line[:MAX_LINE_CHARS]
                    if regex.search(clipped):
                        hits.append(f"{rel}:{number}: {_redact_lines([clipped])[0]}")
                        seen.add(rel)
                        if len(hits) >= self.max_grep_hits:
                            return True
        return False


# ---------------- OpenAPI đầy đủ ----------------

class OpenApiTools:
    """`operation(method, path)` và `schema(name)`: một phần của OpenAPI, đã giải `$ref` (độ sâu tối đa 6, vòng tham chiếu -> {"$cycle": tên})."""

    def __init__(self, spec: dict, *, max_bytes: int = OPENAPI_MAX_BYTES):
        self.spec, self.max_bytes = spec, max_bytes
        self._endpoints = {(e["method"], e["path"]): e for e in openapi.endpoints(spec)} | {(e["method"], e["spec_path"]): e for e in openapi.endpoints(spec)}

    def _lookup(self, ref: str):
        node = self.spec
        for part in ref[2:].split("/"):
            node = node.get(part.replace("~1", "/").replace("~0", "~")) if isinstance(node, dict) else None
        return node

    def _expand(self, node, depth: int, stack: tuple[str, ...]):
        if isinstance(node, list):
            return [self._expand(item, depth, stack) for item in node]
        if isinstance(node, str):
            return node if len(node) <= OPENAPI_MAX_STRING else node[:OPENAPI_MAX_STRING] + "…"
        if not isinstance(node, dict):
            return node
        if "$ref" in node:
            ref = node["$ref"]
            if not isinstance(ref, str) or not ref.startswith("#/"):
                return {"$unresolved": True}
            name = ref.rsplit("/", 1)[-1]
            if ref in stack:
                return {"$cycle": name}
            if depth >= OPENAPI_MAX_DEPTH:
                return {"$truncated": name}
            target = self._lookup(ref)
            return {"$ref_name": name, **self._expand(target, depth + 1, (*stack, ref))} if isinstance(target, dict) else {"$unresolved": True}
        return {key: self._expand(value, depth, stack) for key, value in node.items() if not str(key).startswith("x-")}

    def _out(self, attrs: dict, value) -> ToolOutcome:
        body = json.dumps(value, ensure_ascii=False, sort_keys=True, indent=1)
        if len(body.encode("utf-8")) > self.max_bytes:
            body = body.encode("utf-8")[: self.max_bytes].decode("utf-8", errors="ignore") + "\n… (cắt bớt: hỏi từng schema bằng `schema`)"
        return ToolOutcome(f"<openapi {_attrs(attrs)}>\n{fence(body)}\n</openapi>", categories=frozenset({"api_spec"}))

    def operation(self, method: str, path: str) -> ToolOutcome:
        endpoint = self._endpoints.get((str(method).upper(), path))
        if endpoint is None:
            return ToolOutcome("không có operation này trong OpenAPI", is_error=True)
        op = self.spec["paths"][endpoint["spec_path"]][endpoint["method"].lower()]
        item = self.spec["paths"][endpoint["spec_path"]]
        value = {"method": endpoint["method"], "path": endpoint["path"],
                 **{k: self._expand(op[k], 0, ()) for k in ("summary", "description", "security", "requestBody", "responses") if k in op},
                 "parameters": self._expand([*(item.get("parameters") or []), *(op.get("parameters") or [])], 0, ())}
        return self._out({"method": endpoint["method"], "path": endpoint["path"]}, value)

    def schema(self, name: str) -> ToolOutcome:
        if not isinstance(name, str) or not re.fullmatch(r"[A-Za-z0-9_.\-]{1,100}", name):
            return ToolOutcome("tên schema không hợp lệ", is_error=True)
        for place in (("components", "schemas"), ("definitions",)):
            node = self.spec
            for part in place:
                node = node.get(part) if isinstance(node, dict) else None
            if isinstance(node, dict) and isinstance(node.get(name), dict):
                return self._out({"schema": name}, self._expand(node[name], 0, (f"#/{'/'.join(place)}/{name}",)))
        return ToolOutcome("không có schema này trong OpenAPI", is_error=True)
