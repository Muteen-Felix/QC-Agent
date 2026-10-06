"""Đọc nguồn `COPY`/`ADD` của một Dockerfile và đối chiếu với từng build context ứng viên (S4-08; S4-10, S4-11 dùng lại).

Hàm thuần, chỉ ĐỌC file. Kết quả là CẤU TRÚC (không chỉ bool): mỗi nguồn được phân loại và phân giải tính từ gốc repo, kèm tập context hợp lệ và
`verify` (nội dung `qc-agent:todo VERIFY`) khi không thể kết luận. "Hợp lệ" chỉ có nghĩa mọi nguồn cục bộ ĐỌC ĐƯỢC đều tồn tại tính từ context đó:
đó là bằng chứng để loại trừ, không phải bằng chứng "đây là context đúng".

Bỏ qua (ghi lại kèm lý do, không làm context mất hợp lệ): `--from=...`, URL, nguồn thoát khỏi context. Không phân tích được (=> VERIFY, nếu còn
nhiều hơn một context để chọn): heredoc trong COPY/ADD, biến (`$X`) trong đường dẫn, JSON/dấu nháy hỏng, `# escape=` khác `\\`.
"""
from __future__ import annotations

import json
import posixpath
import re
import shlex
from dataclasses import dataclass
from pathlib import Path

_DIRECTIVE = re.compile(r"^\s*#\s*([A-Za-z]+)\s*=\s*(\S+)\s*$")
_HEREDOC = re.compile(r"(?<!<)<<(?!<)-?\s*([\"']?)([A-Za-z_][\w-]*)\1")   # `<<<` (here-string) không phải heredoc
_FLAG = re.compile(r"\s*(--\S+)\s*")
_URL = re.compile(r"^(?:[A-Za-z][A-Za-z0-9+.-]*://|git@)")
_GLOB = re.compile(r"[*?\[]")


@dataclass(frozen=True)
class CopySource:
    instruction: str          # COPY | ADD
    line: int                 # dòng bắt đầu lệnh trong Dockerfile (từ 1)
    raw: str                  # nguyên văn nguồn
    kind: str                 # path | context (`.`) | glob | skipped | unparsed
    reason: str = ""          # bắt buộc với skipped/unparsed


@dataclass(frozen=True)
class Resolved:
    """Một nguồn tính từ MỘT context cụ thể. `repo_path` tương đối gốc repo (posix), None khi skipped/unparsed."""
    source: CopySource
    kind: str                 # dir | file | context | glob | missing | skipped | unparsed
    repo_path: str | None
    exists: bool              # skipped/unparsed tính True: không dùng để loại context


@dataclass(frozen=True)
class ContextCheck:
    context: str              # tương đối gốc repo, "." là gốc
    resolved: tuple[Resolved, ...]
    valid: bool               # không nguồn nào ở trạng thái missing

    @property
    def missing(self) -> tuple[Resolved, ...]:
        return tuple(item for item in self.resolved if not item.exists)


@dataclass(frozen=True)
class ContextAnalysis:
    dockerfile: str
    sources: tuple[CopySource, ...]
    checks: tuple[ContextCheck, ...]
    valid_contexts: tuple[str, ...]
    chosen: str
    verify: str | None        # có => nơi gọi ghi `qc-agent:todo VERIFY`

    @property
    def unparsed(self) -> tuple[CopySource, ...]:
        return tuple(source for source in self.sources if source.kind == "unparsed")

    @property
    def skipped(self) -> tuple[CopySource, ...]:
        return tuple(source for source in self.sources if source.kind == "skipped")

    def check(self, context: str) -> ContextCheck | None:
        return next((item for item in self.checks if item.context == context), None)

    @property
    def chosen_check(self) -> ContextCheck:
        return self.check(self.chosen)


# ---------- context ứng viên ----------

def context_candidates(dockerfile: str) -> list[str]:
    """Thư mục chứa Dockerfile, rồi các thư mục cha tới gốc repo ("." cuối cùng)."""
    parent = posixpath.dirname(dockerfile)
    found = []
    while parent:
        found.append(parent)
        parent = posixpath.dirname(parent)
    return found + ["."]


def default_context(dockerfile: str) -> str:
    """Giá trị tạm khi không kết luận được: thư mục chứa Dockerfile (riêng `docker/*Dockerfile*` thì gốc repo, như quy ước từ trước)."""
    parts = dockerfile.split("/")
    return "." if len(parts) == 1 or parts[0] == "docker" else "/".join(parts[:-1])


# ---------- phân tích cú pháp ----------

def _logical_lines(text: str) -> tuple[list[tuple[int, str, bool]], list[str]]:
    """[(số dòng, nội dung lệnh đã nối dòng, có heredoc)] và các lý do không phân tích được toàn file."""
    lines = text.splitlines()
    for line in lines:   # parser directive chỉ hợp lệ ở đầu file
        match = _DIRECTIVE.match(line)
        if not match:
            if line.strip():
                break
            continue
        if match.group(1).lower() == "escape" and match.group(2) != "\\":
            return [], [f"# escape={match.group(2)} (nối dòng không phải dấu \\)"]
    out: list[tuple[int, str, bool]] = []
    index = 0
    while index < len(lines):
        start, buffer = index + 1, ""
        while index < len(lines):
            line = lines[index].rstrip("\r")
            index += 1
            if not buffer and (not line.strip() or line.lstrip().startswith("#")):
                break
            if buffer and line.lstrip().startswith("#"):   # comment giữa các dòng nối vẫn được bỏ
                continue
            if line.rstrip().endswith("\\"):
                buffer += line.rstrip()[:-1] + " "
                continue
            buffer += line
            break
        text_line = buffer.strip()
        if not text_line:
            continue
        heredocs = [match.group(2) for match in _HEREDOC.finditer(text_line)]
        for terminator in heredocs:   # bỏ phần thân heredoc tới dòng kết thúc
            while index < len(lines) and lines[index].strip() != terminator:
                index += 1
            index += 1
        out.append((start, text_line, bool(heredocs)))
    return out, []


def _classify(instruction: str, line: int, raw: str) -> CopySource:
    def make(kind: str, reason: str = "") -> CopySource:
        return CopySource(instruction, line, raw, kind, reason)

    if _URL.match(raw):
        return make("skipped", "URL, không phải file của repo")
    if "$" in raw:
        return make("unparsed", "biến ($ARG/$ENV) trong đường dẫn nguồn")
    normal = posixpath.normpath(raw.lstrip("/") or ".")   # nguồn bắt đầu bằng / vẫn tính từ gốc context
    if normal == ".." or normal.startswith("../"):
        return make("skipped", "nguồn thoát khỏi context")
    if _GLOB.search(normal):
        return make("glob")
    return make("context" if normal == "." else "path")


def parse_sources(text: str) -> tuple[CopySource, ...]:
    logical, file_issues = _logical_lines(text)
    sources = [CopySource("DOCKERFILE", 1, "", "unparsed", issue) for issue in file_issues]
    for line, content, heredoc in logical:
        head, rest = (content.split(None, 1) + [""])[:2]   # Dockerfile cho phép tab/nhiều khoảng trắng sau tên lệnh
        instruction = head.upper()
        if instruction not in ("COPY", "ADD"):
            continue
        if heredoc:
            sources.append(CopySource(instruction, line, rest.strip()[:60], "unparsed", "heredoc trong COPY/ADD"))
            continue
        flags = []
        while True:   # cờ đứng TRƯỚC phần nguồn/đích, cả ở dạng JSON: `COPY --chown=1:1 ["a", "/b"]`
            flag = _FLAG.match(rest)
            if not flag:
                break
            flags.append(flag.group(1))
            rest = rest[flag.end():]
        try:
            tokens = json.loads(rest) if rest.startswith("[") else shlex.split(rest)
        except ValueError:
            sources.append(CopySource(instruction, line, rest.strip()[:60], "unparsed", "không tách được đối số (dấu nháy/JSON hỏng)"))
            continue
        if not isinstance(tokens, list) or not all(isinstance(token, str) for token in tokens):
            sources.append(CopySource(instruction, line, rest.strip()[:60], "unparsed", "dạng JSON không phải danh sách chuỗi"))
            continue
        args = tokens
        if len(args) < 2:
            sources.append(CopySource(instruction, line, rest.strip()[:60], "unparsed", "thiếu nguồn hoặc đích"))
            continue
        from_flag = next((flag for flag in flags if flag.startswith("--from")), None)
        for raw in args[:-1]:
            if from_flag:
                sources.append(CopySource(instruction, line, raw, "skipped", f"{from_flag}: nguồn từ stage/image khác, không phải context"))
            else:
                sources.append(_classify(instruction, line, raw))
    return tuple(sources)


# ---------- đối chiếu theo context ----------

def _resolve(root: Path, context: str, source: CopySource) -> Resolved:
    if source.kind in ("skipped", "unparsed"):
        return Resolved(source, source.kind, None, True)
    if source.kind == "context":
        return Resolved(source, "context", context, True)
    relative = posixpath.normpath(source.raw.lstrip("/"))
    repo_path = posixpath.normpath(posixpath.join(context, relative))
    base = root / context
    if source.kind == "glob":
        try:
            found = any(True for _ in base.glob(relative))
        except (OSError, ValueError):
            found = False
        return Resolved(source, "glob" if found else "missing", repo_path, found)
    target = root / repo_path
    if target.is_dir():
        return Resolved(source, "dir", repo_path, True)
    if target.exists():
        return Resolved(source, "file", repo_path, True)
    return Resolved(source, "missing", repo_path, False)


def check_context(root: Path, context: str, sources: tuple[CopySource, ...]) -> ContextCheck:
    resolved = tuple(_resolve(Path(root), context, source) for source in sources)
    return ContextCheck(context, resolved, all(item.exists for item in resolved))


def _describe_missing(check: ContextCheck) -> str:
    first = check.missing[0]
    more = f" (+{len(check.missing) - 1} nguồn khác)" if len(check.missing) > 1 else ""
    return f"{check.context}: thiếu {first.repo_path}{more}"


def analyze(root, dockerfile: str, contexts: list[str] | None = None, *, text: str | None = None) -> ContextAnalysis:
    """Đối chiếu COPY/ADD của `dockerfile` (tương đối gốc repo) với `contexts` (mặc định: thư mục chứa nó rồi các thư mục cha tới gốc).

    Không VERIFY chỉ khi KHÔNG có nguồn không đọc được và đúng một context khiến mọi nguồn tồn tại (kể cả khi chỉ có một context ứng viên:
    Dockerfile ở gốc mà `COPY absent.txt` thì không context nào hợp lệ => VERIFY)."""
    root = Path(root)
    if text is None:
        try:
            text = (root / dockerfile).read_text(encoding="utf-8-sig", errors="replace")
        except OSError:
            text = ""
    candidates = list(contexts) if contexts else context_candidates(dockerfile)
    sources = parse_sources(text)
    checks = tuple(check_context(root, context, sources) for context in candidates)
    valid = tuple(check.context for check in checks if check.valid)
    default = default_context(dockerfile)
    fallback = default if default in candidates else candidates[0]
    unparsed = [source for source in sources if source.kind == "unparsed"]
    options = ", ".join(candidates)
    if unparsed:
        why = "; ".join(f"{source.instruction} dòng {source.line}: {source.reason}" for source in unparsed[:3])
        verify = (f"không suy được context của {dockerfile} ({why}); tạm dùng {fallback} trong [{options}] (gợi ý, chưa xác nhận): "
                  f"đối chiếu COPY/ADD rồi đặt --sut-context")
        return ContextAnalysis(dockerfile, sources, checks, valid, fallback, verify)
    if len(valid) == 1:
        return ContextAnalysis(dockerfile, sources, checks, valid, valid[0], None)
    if valid:
        chosen = fallback if fallback in valid else valid[0]
        verify = (f"chọn context {chosen} trong [{', '.join(valid)}] (gợi ý, chưa xác nhận): COPY/ADD của {dockerfile} hợp lệ ở cả {len(valid)} context, "
                  f"không đủ dữ kiện để chọn một")
        return ContextAnalysis(dockerfile, sources, checks, valid, chosen, verify)
    why = "; ".join(_describe_missing(check) for check in checks[:3])
    verify = (f"không context nào trong [{options}] có đủ nguồn COPY/ADD của {dockerfile} ({why}); tạm dùng {fallback} (gợi ý, chưa xác nhận): "
              f"kiểm lại Dockerfile và đặt --sut-context")
    return ContextAnalysis(dockerfile, sources, checks, valid, fallback, verify)
