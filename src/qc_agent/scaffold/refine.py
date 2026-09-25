"""`qc-agent init --refine`: Pha 2 của onboarding (plan 16) — điền các vùng REFINE bằng dữ liệu của SUT ĐANG CHẠY. Thường chạy trên CI của repo SUT
(bên trong qc-gate.reusable.yml), nhưng chạy cục bộ được.

Đọc repo SUT **chỉ-đọc** và chỉ viết lại vùng nằm giữa `qc-agent:begin refine <tên>` ... `qc-agent:end`; người đã xoá marker thì vùng đó thuộc về người và không bị đụng vào.
Không tự push: đầu ra là `refine.patch` (unified diff, `git apply`-được) và `suggestions.json` ([{path, start_line, end_line, replacement}], chỉ vùng <= MAX_SUGGEST_LINES
dòng) để bước sau đăng thành review ```suggestion```. Cùng OpenAPI thì cùng đầu ra (xếp ổn định), để so hash và không đăng lặp mỗi lần push.

Việc làm: `exclude_path` (Schemathesis) và endpoint k6 từ `openapi.analyze` (bộ lọc bước 25-26 giữ nguyên); so `sut_health_path` của qc.yml với đường dẫn trong OpenAPI sống
và đề xuất sửa nếu lệch; `--suggest-ui` chỉ thay `explore.yaml` khi nó vẫn còn là khung TODO của Pha 1 (giữ dấu SUGGESTED và log egress, ghi vào --out, không vào repo).
Exit: 0 = xong (kể cả không có gì để đổi), 3 = tham số/nguồn OpenAPI sai.
"""
from __future__ import annotations

import argparse
import difflib
import json
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

from qc_agent.scaffold import openapi, suggest
from qc_agent.scaffold import templates as t

SYSTEM_ERROR = 3
MAX_SUGGEST_LINES = 40
WORKFLOW = ".github/workflows/qc.yml"
EXPLORE_FLOW = ".qc-agent/midscene/explore.yaml"
SCAN_DIRS = (".qc-agent",)
SCAN_FILES = (WORKFLOW,)
SUFFIXES = (".yaml", ".yml", ".js", ".mjs")

_BEGIN = re.compile(r"^(?P<indent>\s*)(?P<comment>#|//)\s*" + re.escape(t.REFINE_BEGIN) + r"\s+(?P<name>[a-z0-9_]+)\s*$")
_END = re.compile(r"^\s*(?:#|//)\s*" + re.escape(t.REFINE_END) + r"\s*$")
_HEALTH_LINE = re.compile(r"^(?P<head>\s*sut_health_path:\s*)(?P<value>\"[^\"]*\"|'[^']*'|\S+)(?P<tail>.*)$")


class RefineError(ValueError):
    """Tham số hoặc nguồn dữ liệu không dùng được."""


@dataclass(frozen=True)
class Region:
    path: str
    name: str
    start: int          # chỉ số dòng (0-based) của marker begin
    end: int            # chỉ số dòng (0-based) của marker end
    indent: str
    comment: str


@dataclass
class Result:
    patch: str = ""
    suggestions: list[dict] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    changed: list[str] = field(default_factory=list)


def _files(root: Path) -> list[str]:
    found = [rel for rel in SCAN_FILES if (root / rel).is_file()]
    for folder in SCAN_DIRS:
        base = root / folder
        if base.is_dir():
            found += sorted(p.relative_to(root).as_posix() for p in base.rglob("*") if p.is_file() and p.suffix in SUFFIXES)
    return sorted(dict.fromkeys(found))


def _read_lines(path: Path) -> list[str] | None:
    """None nếu không đọc được (quyền, mã hoá): KHÔNG im lặng coi như file rỗng."""
    try:
        with open(path, encoding="utf-8", newline="") as handle:     # giữ nguyên CRLF/LF của file (Path.read_text chưa có newline= ở Python 3.11)
            return handle.read().splitlines(keepends=True)
    except (OSError, UnicodeError):
        return None


def find_regions(rel: str, lines: list[str]) -> list[Region]:
    regions, open_at = [], None
    for index, raw in enumerate(lines):
        line = raw.rstrip("\r\n")
        begin = _BEGIN.match(line)
        if begin and open_at is None:
            open_at = (index, begin)
        elif open_at is not None and _END.match(line):
            start, match = open_at
            regions.append(Region(rel, match["name"], start, index, match["indent"], match["comment"]))
            open_at = None
    return regions


def _exclude_region(region: Region, analysis: openapi.Analysis) -> list[str]:
    i, c = region.indent, region.comment
    body = ([f"{i}exclude_path:"] + [f"{i}- {json.dumps(path, ensure_ascii=False)}  # {' '.join(reason.split())[:120].replace('#', '')}".rstrip()
                                     for path, reason in sorted(analysis.exclude)]) if analysis.exclude else [
        f"{i}{c} (OpenAPI sống: không có endpoint nào cần loại khỏi fuzz)"]
    return [f"{i}{c} {t.REFINE_BEGIN} exclude_path", *body, f"{i}{c} {t.REFINE_END}"]


def _k6_region(region: Region, analysis: openapi.Analysis) -> list[str] | None:
    if not analysis.get_paths:
        return None       # không có GET nào dùng được: giữ danh sách tạm, người quyết định
    i, c = region.indent, region.comment
    return [f"{i}{c} {t.REFINE_BEGIN} k6_paths", f"{i}const PATHS = {json.dumps(analysis.get_paths, ensure_ascii=False)};", f"{i}{c} {t.REFINE_END}"]


def _health_fix(lines: list[str], spec_paths: list[str]) -> tuple[int, str, str] | None:
    """(chỉ số dòng, dòng mới, đường dẫn đề xuất) nếu sut_health_path của qc.yml không có trong OpenAPI mà có đúng MỘT đường dẫn health kết thúc bằng nó (vd. /health -> /api/health)."""
    for index, raw in enumerate(lines):
        match = _HEALTH_LINE.match(raw.rstrip("\r\n"))
        if not match:
            continue
        current = match["value"].strip("\"'")
        if current in spec_paths or current == "/":
            return None
        candidates = sorted(p for p in spec_paths if p.endswith(current) and "{" not in p)
        if len(candidates) != 1:
            return None
        return index, f"{match['head']}{json.dumps(candidates[0])}", candidates[0]
    return None


def _spec_get_paths(spec: dict, prefix: str) -> list[str]:
    paths = spec.get("paths") if isinstance(spec.get("paths"), dict) else {}
    return sorted(prefix + p for p, item in paths.items() if isinstance(item, dict) and "get" in item)


def refine(root, openapi_source: str, *, ui_urls: list[str] | None = None, suggest_ui: bool = False, out_dir=None) -> Result:
    root = Path(root)
    if not root.is_dir():
        raise RefineError(f"--sut-root không phải thư mục: {root}")
    try:
        spec = openapi.load(openapi_source)
        analysis = openapi.analyze(spec)
    except openapi.OpenApiError as error:
        raise RefineError(str(error)) from None
    result = Result()
    result.notes.extend(analysis.warnings)
    api_paths = _spec_get_paths(spec, analysis.prefix)
    patch_parts: list[str] = []
    for rel in _files(root):
        lines = _read_lines(root / rel)
        if lines is None:
            result.notes.append(f"không đọc được {rel} (quyền/mã hoá): bỏ qua")
            continue
        new = list(lines)
        eol = "\r\n" if lines and lines[0].endswith("\r\n") else "\n"
        edits: list[tuple[int, int, list[str]]] = []      # (start, end inclusive, dòng mới) theo chỉ số dòng cũ
        for region in find_regions(rel, lines):
            if region.name == "exclude_path":
                replacement = _exclude_region(region, analysis)
            elif region.name == "k6_paths":
                replacement = _k6_region(region, analysis)
            else:
                replacement = None
            if replacement is None:
                continue
            old = [line.rstrip("\r\n") for line in lines[region.start:region.end + 1]]
            if old != replacement:
                edits.append((region.start, region.end, replacement))
        if rel == WORKFLOW:
            fix = _health_fix(lines, api_paths)
            if fix:
                edits.append((fix[0], fix[0], [fix[1]]))
                result.notes.append(f"sut_health_path lệch OpenAPI sống: đề xuất {fix[2]}")
        for start, end, replacement in sorted(edits, reverse=True):
            new[start:end + 1] = [line + eol for line in replacement]
        if rel == EXPLORE_FLOW and suggest_ui:
            explore = _suggest_explore(root, lines, ui_urls or [], out_dir, result)
            if explore is not None:
                new = explore.splitlines(keepends=True)
                edits.append((0, max(len(lines) - 1, 0), [line.rstrip("\r\n") for line in new]))
        if new == lines:
            continue
        result.changed.append(rel)
        patch_parts.append("".join(difflib.unified_diff(lines, new, f"a/{rel}", f"b/{rel}")))
        for start, end, replacement in sorted(edits):
            if end - start + 1 <= MAX_SUGGEST_LINES and len(replacement) <= MAX_SUGGEST_LINES:
                result.suggestions.append({"path": rel, "start_line": start + 1, "end_line": end + 1, "replacement": "\n".join(replacement)})
    result.patch = "".join(patch_parts)
    return result


def _suggest_explore(root: Path, lines: list[str], ui_urls: list[str], out_dir, result: Result) -> str | None:
    """Chỉ thay khi explore.yaml VẪN là khung TODO của Pha 1 (người đã sửa thì không đụng vào)."""
    if "".join(lines).replace("\r\n", "\n").rstrip("\n") != t.midscene_explore_flow().rstrip("\n"):
        result.notes.append(f"--suggest-ui: {EXPLORE_FLOW} không còn là khung TODO, giữ nguyên")
        return None
    if not ui_urls:
        result.notes.append("--suggest-ui: thiếu --ui-url, bỏ qua")
        return None
    if out_dir is None:
        raise RefineError("--suggest-ui cần --out (nơi ghi log egress; repo SUT là chỉ-đọc)")
    try:
        flows, model = suggest.suggest_flows(ui_urls, Path(out_dir))
    except suggest.SuggestError as error:
        result.notes.append(f"--suggest-ui: {error}; giữ khung TODO")
        return None
    result.notes.append(f"LLM ({model}) gợi ý {len(flows)} flow; log egress ở {Path(out_dir) / '.qc-agent' / 'egress.jsonl'}")
    return t.midscene_explore_flow(tasks=flows, suggested_by=model)


class _Parser(argparse.ArgumentParser):
    def error(self, message):
        raise RefineError(f"tham số sai: {message}")


def main(argv: list[str]) -> int:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")
    ap = _Parser(prog="qc-agent init --refine", description="Điền vùng REFINE từ OpenAPI của SUT đang chạy (chỉ-đọc repo, ghi refine.patch + suggestions.json).")
    ap.add_argument("--sut-root", metavar="DIR", default=".", help="repo SUT (chỉ đọc)")
    ap.add_argument("--openapi", required=True, metavar="FILE|URL", help="OpenAPI của SUT đang chạy, vd. $APP_BASE_URL/openapi.json")
    ap.add_argument("--ui-url", action="append", default=[], metavar="URL", help="URL UI đang chạy (cho --suggest-ui)")
    ap.add_argument("--suggest-ui", action="store_true", help="LLM gợi ý flow explore (chỉ khi explore.yaml còn là khung TODO); gửi nhãn UI ra ngoài, có log egress")
    ap.add_argument("--out", metavar="DIR", default="refine-out", help="thư mục ghi refine.patch, suggestions.json (mặc định refine-out)")
    try:
        args = ap.parse_args(argv)
        result = refine(args.sut_root, args.openapi, ui_urls=args.ui_url, suggest_ui=args.suggest_ui, out_dir=args.out)
        out = Path(args.out)
        out.mkdir(parents=True, exist_ok=True)
        (out / "refine.patch").write_text(result.patch, encoding="utf-8", newline="")
        (out / "suggestions.json").write_text(json.dumps(result.suggestions, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    except SystemExit as exit_:
        return exit_.code if isinstance(exit_.code, int) else 0
    except RefineError as error:
        print(f"LỖI: {error}", file=sys.stderr)
        return SYSTEM_ERROR
    for note in result.notes:
        print(f"· {note}")
    print(f"refine: {len(result.changed)} file đổi ({', '.join(result.changed) or 'không có gì để đề xuất'}); {len(result.suggestions)} gợi ý; ghi vào {out}")
    return 0
