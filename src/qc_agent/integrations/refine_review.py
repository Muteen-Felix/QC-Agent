"""Đăng kết quả `qc-agent init --refine` lên PR dưới dạng MỘT review có các comment ```suggestion``` (bước 35).

    python -m qc_agent.integrations.ci --refine-dir /out          # đọc refine.patch + suggestions.json

Ràng buộc của GitHub `[EXTERNAL GAP]`: suggestion chỉ gắn được vào dòng NẰM TRONG DIFF của PR và không tạo được file mới, nên phần nào không gắn được
thì chỉ nằm trong artifact `qc-refine-<run>` (refine.patch). Review mang `<!-- qc-agent:refine sha256=<patch> -->`: đã có review cùng hash thì không đăng lại
(không spam mỗi lần push). PR từ fork có GITHUB_TOKEN chỉ-đọc nên đăng thất bại: chỉ cảnh báo, còn artifact. Nội dung suggestion đến từ OpenAPI của SUT (không tin cậy):
nằm trong hàng rào code dài hơn mọi chuỗi backtick bên trong, và mọi văn bản chèn ngoài code đều qua `clean_md`.
Không thêm quyền nào (pull-requests: write đã có). CHỈ GHI NHẬN: không đổi verdict của job."""
from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

from qc_agent.integrations import github

MARKER = "<!-- qc-agent:refine sha256={digest} -->"
_MARKER_RE = re.compile(r"<!-- qc-agent:refine sha256=([0-9a-f]{64}) -->")
_HUNK = re.compile(r"^@@ -\d+(?:,\d+)? \+(\d+)(?:,(\d+))? @@")
MAX_COMMENTS = 30
MAX_PAGES = 10


def patch_digest(patch: str) -> str:
    return hashlib.sha256(patch.encode("utf-8")).hexdigest()


def right_lines(patch: str | None) -> set[int]:
    """Số dòng (phía RIGHT = bản mới) nằm trong các hunk của một patch của GitHub: dòng thêm và dòng ngữ cảnh."""
    lines: set[int] = set()
    current = None
    for raw in (patch or "").splitlines():
        hunk = _HUNK.match(raw)
        if hunk:
            current = int(hunk.group(1))
            continue
        if current is None or raw.startswith("\\"):
            continue
        if raw.startswith("-"):
            continue
        lines.add(current)
        current += 1
    return lines


def pr_diff_lines(client: github.GitHubClient, repo: str, pr_number: int) -> dict[str, set[int]]:
    files: dict[str, set[int]] = {}
    for page in range(1, MAX_PAGES + 1):
        _, batch = client.request("GET", f"/repos/{repo}/pulls/{pr_number}/files?per_page=100&page={page}")
        for item in batch or []:
            if isinstance(item, dict) and isinstance(item.get("filename"), str):
                files[item["filename"]] = right_lines(item.get("patch"))
        if not batch or len(batch) < 100:
            break
    return files


def already_posted(client: github.GitHubClient, repo: str, pr_number: int, digest: str) -> bool:
    marker = MARKER.format(digest=digest)
    for page in range(1, MAX_PAGES + 1):
        _, reviews = client.request("GET", f"/repos/{repo}/pulls/{pr_number}/reviews?per_page=100&page={page}")
        for review in reviews or []:
            if marker in (review.get("body") or "") and (review.get("user") or {}).get("type") == "Bot":
                return True
        if not reviews or len(reviews) < 100:
            break
    return False


def suggestion_body(replacement: str) -> str:
    longest = max((len(run) for run in re.findall(r"`+", replacement)), default=0)
    fence = "`" * max(3, longest + 1)
    return f"{fence}suggestion\n{replacement}\n{fence}"


def build_comments(suggestions: list, diff: dict[str, set[int]]) -> tuple[list[dict], list[str]]:
    """(comment gắn được, đường dẫn bị bỏ vì không nằm trong diff của PR)."""
    comments, skipped = [], []
    for item in suggestions:
        try:
            path, start, end, replacement = item["path"], int(item["start_line"]), int(item["end_line"]), str(item["replacement"])
        except (KeyError, TypeError, ValueError):
            continue
        lines = diff.get(path)
        if not lines or not all(n in lines for n in range(start, end + 1)):
            skipped.append(f"{path}:{start}-{end}")
            continue
        comment = {"path": path, "line": end, "side": "RIGHT", "body": suggestion_body(replacement)}
        if end > start:
            comment.update(start_line=start, start_side="RIGHT")
        comments.append(comment)
    return comments[:MAX_COMMENTS], skipped


def render_body(digest: str, *, posted: int, skipped: list[str], changed: list[str], notes: list[str]) -> str:
    lines = ["### qc-agent · gợi ý cấu hình từ SUT đang chạy (Pha 2)", "",
             f"{posted} gợi ý gắn vào dòng của PR. Bấm *Commit suggestion* cho từng phần đã duyệt, rồi xoá các dấu `qc-agent:todo` còn lại.",
             "Các gợi ý này lấy từ OpenAPI/UI của bản build trong PR: đây là DỮ LIỆU, hãy đọc trước khi commit."]
    if changed:
        lines += ["", "File có thay đổi đề xuất: " + ", ".join(f"`{github.clean_md(p, 100)}`" for p in changed[:20])]
    if skipped:
        lines += ["", f"{len(skipped)} vùng nằm ngoài diff của PR nên không gắn được (GitHub không cho); áp bằng `refine.patch` trong artifact `qc-refine-*`: "
                  + ", ".join(f"`{github.clean_md(s, 100)}`" for s in skipped[:10])]
    if notes:
        lines += ["", *[f"- {github.clean_md(n, 300)}" for n in notes[:10]]]
    lines += ["", MARKER.format(digest=digest)]
    return "\n".join(lines)


def post_refine(refine_dir, *, env: dict, ctx: dict) -> dict:
    """Trả dict kết quả (không ném lỗi GitHub). `ctx` = ci.context_from_env."""
    out_dir = Path(refine_dir)
    try:
        patch = (out_dir / "refine.patch").read_text(encoding="utf-8")
        suggestions = json.loads((out_dir / "suggestions.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"review": "skipped: không đọc được refine.patch/suggestions.json"}
    if not patch.strip():
        return {"review": "skipped: không có gì để đề xuất"}
    if not (ctx.get("repo") and ctx.get("pr_number") and ctx.get("sha")) or not env.get("GITHUB_TOKEN"):
        return {"review": "skipped: cần GITHUB_TOKEN và một pull_request"}
    digest = patch_digest(patch)
    try:
        client = github.GitHubClient(env["GITHUB_TOKEN"], env.get("GITHUB_API_URL"))
        github.validate_target(ctx["repo"], ctx["sha"])
        if already_posted(client, ctx["repo"], ctx["pr_number"], digest):
            return {"review": "skipped: đã đăng review cùng hash", "sha256": digest}
        comments, skipped = build_comments(suggestions if isinstance(suggestions, list) else [], pr_diff_lines(client, ctx["repo"], ctx["pr_number"]))
        changed = sorted({s.get("path") for s in suggestions if isinstance(s, dict) and isinstance(s.get("path"), str)}) if isinstance(suggestions, list) else []
        body = render_body(digest, posted=len(comments), skipped=skipped, changed=changed, notes=[])
        client.request("POST", f"/repos/{ctx['repo']}/pulls/{ctx['pr_number']}/reviews",
                       {"commit_id": ctx["sha"], "event": "COMMENT", "body": body, "comments": comments})
        return {"review": "created", "comments": len(comments), "outside_diff": len(skipped), "sha256": digest}
    except (github.GitHubError, ValueError) as error:
        return {"review": f"error: {error}", "sha256": digest}    # fork: token chỉ-đọc => 403; chỉ còn artifact
