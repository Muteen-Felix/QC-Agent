"""Parse PRD thành story / AC / endpoint bằng code TẤT ĐỊNH (S1-02): cùng file vào cho ra đúng cùng kết quả, không LLM, không mạng
(trừ khi được cho URL OpenAPI). PRD là dữ liệu KHÔNG TIN CẬY: module này không thực thi gì từ nội dung, không log nội dung
(không import logging), và mọi thông điệp lỗi/warning chỉ có số đếm hoặc ID đã qua regex, không bao giờ trích lại văn bản của PRD.

Ba định dạng:
  - markdown : chia theo heading; story = heading `US-<n>` / `User Story` / `Story <n>` / `Câu chuyện…` (hoặc dòng `US-<n>: …`);
               AC dưới heading `Acceptance Criteria` / `AC` / `Tiêu chí chấp nhận` ở dạng `AC-<n>(.<n>)*: …`, list item, hoặc Given/When/Then.
  - openapi  : file .json/.yaml/.yml có khoá `openapi`/`swagger`; chỉ lấy endpoint (không có story/AC).
  - text     : còn lại; một story `S-1` duy nhất, không có AC (fallback).
ID: ưu tiên ID tường minh trong PRD. Thiếu thì `AC-<sha1(text chuẩn hoá)[:8]>` (đổi thứ tự không làm lệch ID) kèm một warning khuyên BA ghi ID.
`sha256` tính trên bytes đã chuẩn hoá (bỏ BOM, CRLF -> LF); khác `core/project.file_sha256` đúng ở chỗ bỏ BOM.
"""
from __future__ import annotations

import hashlib
import re
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path

import yaml

from qc_agent.scaffold import openapi

MAX_BYTES = 256 * 1024
TITLE_MAX = 200
FALLBACK_STORY = "S-1"

_HEADING = re.compile(r"^(#{1,6})[ \t]+(.+?)[ \t]*#*[ \t]*$")
_FENCE = re.compile(r"^\s*(?:```|~~~)")
_LIST = re.compile(r"^(\s*)(?:[-*+]|\d+[.)])\s+(.*)$")
_STORY_LABEL = re.compile(r"^(?:user\s+story|story|câu\s+chuyện(?:\s+người\s+dùng)?)\b", re.I)   # số ít: "Stories" (mục chứa) không khớp
_STORY_ID = re.compile(r"\bUS[-_]?(\d+)\b", re.I)
_STORY_ID_START = re.compile(r"^\W*US[-_]?\d+\b", re.I)   # US-<n> chỉ tính là story khi đứng ĐẦU heading ("Phạm vi (US-1…US-3)" thì không)
_STORY_NUM = re.compile(r"\bstory\s*#?(\d+)\b", re.I)
_STORY_LINE = re.compile(r"^[\s>]*(?:[-*+]\s+)?(?:\*\*|__)?\s*(US[-_]?\d+)\s*(?:\*\*|__)?\s*[:.)\-–—]\s*(.+)$", re.I)
_AC_HEADING = re.compile(r"^(?:acceptance\s+criteria|ac|tiêu\s+chí\s+(?:chấp\s+nhận|nghiệm\s+thu))\b", re.I)
_AC_ID = re.compile(r"^[\s>]*(?:[-*+]\s+|\d+[.)]\s+)?(?:\*\*|__)?\s*(AC[-_]?\d+(?:\.\d+)*)(?:\*\*|__)?\s*[:.)\-–—]?\s*(?:\*\*|__)?\s*(.*)$", re.I)
_AC_ID_HEADING = re.compile(r"^AC[-_]?\d", re.I)
_GWT_START = re.compile(r"^given\b", re.I)
_GWT_CONT = re.compile(r"^(?:when|then|and|but)\b", re.I)


class GTInputError(ValueError):
    """PRD không đọc được, quá cỡ, không phải UTF-8 hoặc có ID trùng. CLI sẽ trả exit 3. Thông điệp không chứa nội dung PRD."""


@dataclass(frozen=True)
class AC:
    ac_id: str
    text: str = field(repr=False)     # repr chỉ có ID: lỡ tay in đối tượng ra log cũng không lộ văn bản PRD


@dataclass(frozen=True)
class Story:
    story_id: str
    title: str = field(repr=False)
    acs: tuple[AC, ...] = ()


@dataclass(frozen=True)
class ParsedPRD:
    prd_id: str
    sha256: str
    format: str                                   # markdown | openapi | text
    stories: tuple[Story, ...]
    endpoints: tuple[dict, ...]                   # scaffold.openapi.endpoints(); rỗng nếu PRD không kèm OpenAPI
    warnings: tuple[str, ...]
    text: str = field(default="", repr=False, compare=False)   # văn bản đã chuẩn hoá cho S1-04 đưa vào <prd>; ngoài repr để không lọt vào log


# ---------------- chuẩn hoá ----------------

def _normalize(raw: bytes) -> bytes:
    if raw.startswith(b"\xef\xbb\xbf"):
        raw = raw[3:]
    return raw.replace(b"\r\n", b"\n")


def slug(value: str) -> str:
    """`[a-z0-9-]`, bỏ dấu tiếng Việt; rỗng -> "prd"."""
    text = unicodedata.normalize("NFKD", str(value).replace("đ", "d").replace("Đ", "D"))
    text = "".join(ch for ch in text if not unicodedata.combining(ch)).lower()
    return re.sub(r"[^a-z0-9]+", "-", text).strip("-")[:63].strip("-") or "prd"


def _clean(text: str) -> str:
    return " ".join(text.replace("**", "").replace("__", "").split())


def _fingerprint(text: str) -> str:
    folded = " ".join(unicodedata.normalize("NFKC", text).casefold().split()).rstrip(".;:,! ")
    return hashlib.sha1(folded.encode("utf-8")).hexdigest()[:8]


def _split_front_matter(text: str, warnings: list[str]) -> tuple[dict, str]:
    if not text.startswith("---\n"):
        return {}, text
    end = text.find("\n---", 3)
    if end == -1 or text[end + 4:end + 5] not in ("", "\n"):
        return {}, text
    try:
        data = yaml.safe_load(text[4:end])
    except yaml.YAMLError:
        warnings.append("front-matter YAML không hợp lệ: bỏ qua")
        return {}, text[end + 4:].lstrip("\n")
    return (data if isinstance(data, dict) else {}), text[end + 4:].lstrip("\n")


# ---------------- Markdown ----------------

class _AC:
    def __init__(self, explicit: str | None, text: str, indent: int, sticky: bool = False):
        self.explicit = explicit
        self.indent = indent
        self.sticky = sticky   # AC viết dạng heading: dòng trống không kết thúc nó, mọi đoạn/bullet sau đó là mô tả cho tới heading kế tiếp
        self.parts = [text] if text else []

    def add(self, text: str) -> None:
        self.parts.append(text)


class _Story:
    def __init__(self, explicit: str | None, title: str, level: int | None):
        self.explicit, self.title, self.level = explicit, title, level
        self.acs: list[_AC] = []


def _norm_id(raw: str, prefix: str) -> str:
    return re.sub(r"[-_ ]+", "-", raw.strip()).upper() if raw else prefix


def _story_from_heading(text: str) -> tuple[str | None, str] | None:
    if not (_STORY_LABEL.match(text) or _STORY_ID_START.match(text)):
        return None
    found = _STORY_ID.search(text) or _STORY_NUM.search(text)
    explicit = f"US-{found.group(1)}" if found else None
    title = _STORY_LABEL.sub("", text, count=1).strip()
    title = re.sub(r"^\s*(?:US[-_]?\d+|\d+)\s*", "", title, count=1, flags=re.I).lstrip(" :.-–—)").strip()
    return explicit, _clean(title or text)[:TITLE_MAX]


def _parse_markdown(body: str, warnings: list[str]) -> list[_Story]:
    stories: list[_Story] = []
    current: _Story | None = None
    in_ac, ac_level = False, 0
    ac: _AC | None = None
    fenced = False

    def ensure_story() -> _Story:
        nonlocal current
        if current is None:
            current = _Story(FALLBACK_STORY, "Yêu cầu chung", None)
            stories.append(current)
            warnings.append("có AC nằm ngoài mọi user story: gom vào story S-1")
        return current

    def new_ac(explicit: str | None, text: str, indent: int = 0, sticky: bool = False) -> None:
        nonlocal ac
        ac = _AC(explicit, text, indent, sticky)
        ensure_story().acs.append(ac)

    for line in body.split("\n"):
        if _FENCE.match(line):
            fenced = not fenced
            ac = None
            continue
        if fenced:
            continue
        heading = _HEADING.match(line)
        if heading:
            level, text = len(heading.group(1)), heading.group(2).strip()
            ac = None
            if _AC_ID_HEADING.match(text):                 # AC viết dưới dạng heading: "#### AC-1.1 …"
                found = _AC_ID.match(text)
                if found:
                    new_ac(_norm_id(found.group(1), ""), _clean(found.group(2)), sticky=True)
                continue
            story = _story_from_heading(text)
            if story:
                current = _Story(story[0], story[1], level)
                stories.append(current)
                in_ac = False
            elif _AC_HEADING.match(text):
                in_ac, ac_level = True, level
            else:
                if in_ac and level <= ac_level:
                    in_ac = False
                if current is not None and current.level is not None and level <= current.level:
                    current, in_ac = None, False
            continue

        story_line = _STORY_LINE.match(line)
        if story_line and not _AC_ID.match(line):
            current = _Story(_norm_id(story_line.group(1), ""), _clean(story_line.group(2))[:TITLE_MAX], None)
            stories.append(current)
            in_ac, ac = False, None
            continue

        indent = len(line) - len(line.lstrip())
        found = _AC_ID.match(line)
        if found:
            new_ac(_norm_id(found.group(1), ""), _clean(found.group(2)), indent)
            continue
        stripped = line.strip()
        if not stripped:
            if ac is not None and ac.parts and not ac.sticky:
                ac = None                                   # dòng trống kết thúc AC (AC chưa có chữ thì chờ đoạn mô tả kế tiếp)
            continue
        item = _LIST.match(line)
        text = _clean(item.group(2) if item else stripped)
        if item:
            if not in_ac and not (ac is not None and ac.sticky):
                continue
            if ac is not None and (ac.sticky or indent > ac.indent or _GWT_CONT.match(text)):   # bullet lồng sâu hơn, When/Then/And, hoặc mô tả của AC dạng heading
                ac.add(text)
            else:
                new_ac(None, text, indent)
        elif in_ac and _GWT_START.match(text):
            new_ac(None, text, indent)
        elif ac is not None:                                # dòng nối tiếp của AC đang mở
            ac.add(text)
    return stories


def _finalize(raw: list[_Story], warnings: list[str]) -> tuple[Story, ...]:
    explicit_stories: dict[str, int] = {}
    explicit_acs: dict[str, int] = {}
    for story in raw:
        if story.explicit:
            explicit_stories[story.explicit] = explicit_stories.get(story.explicit, 0) + 1
        for ac in story.acs:
            if ac.explicit:
                explicit_acs[ac.explicit] = explicit_acs.get(ac.explicit, 0) + 1
    dup_stories = sorted(k for k, n in explicit_stories.items() if n > 1)
    dup_acs = sorted(k for k, n in explicit_acs.items() if n > 1)
    if dup_stories or dup_acs:
        raise GTInputError("ID trùng trong PRD: " + ", ".join([*dup_stories, *dup_acs])[:300])   # ID đã qua regex, không phải văn bản tự do

    used_stories, used_acs = set(explicit_stories), set(explicit_acs)
    derived_stories = derived_acs = empty = 0

    def unique(base: str, used: set[str]) -> str:
        candidate, n = base, 1
        while candidate in used:
            n += 1
            candidate = f"{base}-{n}"
        used.add(candidate)
        return candidate

    stories: list[Story] = []
    for story in raw:
        if story.explicit:
            story_id = story.explicit
        else:
            derived_stories += 1
            story_id = unique(f"US-{_fingerprint(story.title)}", used_stories)
        acs: list[AC] = []
        for ac in story.acs:
            text = _clean(" ".join(ac.parts))
            if not text:
                continue
            if ac.explicit:
                ac_id = ac.explicit
            else:
                derived_acs += 1
                ac_id = unique(f"AC-{_fingerprint(text)}", used_acs)
            acs.append(AC(ac_id, text))
        if not acs:
            empty += 1
        stories.append(Story(story_id, story.title, tuple(acs)))
    if derived_acs:
        warnings.append(f"{derived_acs} AC không có ID tường minh: ID sinh theo nội dung; nên ghi ID (AC-<n>) trong PRD để ID không đổi khi sửa chữ")
    if derived_stories:
        warnings.append(f"{derived_stories} user story không có ID tường minh: ID sinh theo tiêu đề; nên ghi ID (US-<n>) trong PRD")
    if empty:
        warnings.append(f"{empty} user story không có AC nào")
    return tuple(stories)


# ---------------- điểm vào ----------------

def _fallback(text: str, prd_id: str) -> Story:
    first = next((line.strip() for line in text.split("\n") if line.strip()), "")
    return Story(FALLBACK_STORY, (_clean(first.lstrip("# ").strip()) or prd_id)[:TITLE_MAX], ())


def parse_prd(path: Path, *, openapi_source: str | None = None) -> ParsedPRD:
    path = Path(path)
    try:
        raw = path.read_bytes()
    except OSError as error:
        raise GTInputError(f"không đọc được PRD {path.name}: {error.strerror or type(error).__name__}") from None
    if len(raw) > MAX_BYTES:
        raise GTInputError(f"PRD {path.name} lớn {len(raw)} byte, vượt giới hạn {MAX_BYTES}")
    normalized = _normalize(raw)
    try:
        text = normalized.decode("utf-8")
    except UnicodeDecodeError:
        raise GTInputError(f"PRD {path.name} không phải UTF-8") from None
    sha256 = hashlib.sha256(normalized).hexdigest()
    warnings: list[str] = []
    front, body = _split_front_matter(text, warnings)
    raw_id = front.get("id")
    prd_id = slug(str(raw_id) if raw_id not in (None, "") else path.stem)

    endpoints: tuple[dict, ...] = ()
    fmt = "text"
    if path.suffix.lower() in (".json", ".yaml", ".yml"):
        try:
            candidate = yaml.safe_load(text)
        except yaml.YAMLError:
            candidate = None
        if isinstance(candidate, dict) and ("openapi" in candidate or "swagger" in candidate):
            try:
                spec = openapi.load(str(path))
            except openapi.OpenApiError as error:
                raise GTInputError(str(error)) from None
            fmt, endpoints = "openapi", tuple(openapi.endpoints(spec))
            if openapi_source:
                warnings.append("openapi_source bị bỏ qua vì PRD đã là tài liệu OpenAPI")
            warnings.append("OpenAPI không có user story/AC: chỉ dùng làm danh sách endpoint")
            return ParsedPRD(prd_id, sha256, fmt, (), endpoints, tuple(warnings), text)
    if path.suffix.lower() in (".md", ".markdown") or any(_HEADING.match(line) for line in body.split("\n")):
        fmt = "markdown"

    stories: tuple[Story, ...] = ()
    if fmt == "markdown":
        stories = _finalize(_parse_markdown(body, warnings), warnings)
    if not stories:
        warnings.append("không nhận ra user story/AC nào: coi cả tài liệu là một khối (S-1), không có AC")
        stories = (_fallback(body, prd_id),)
    if openapi_source:
        try:
            endpoints = tuple(openapi.endpoints(openapi.load(openapi_source)))
        except openapi.OpenApiError as error:
            raise GTInputError(str(error)) from None
    return ParsedPRD(prd_id, sha256, fmt, stories, endpoints, tuple(warnings), text)
