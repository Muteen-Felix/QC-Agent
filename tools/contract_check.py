"""CI job `contract-check`: cổng duyệt thay đổi contract theo mức rủi ro.

  không đổi file contract           -> PASS
  PATCH/MINOR (thêm field an toàn)  -> lock đã bump đúng mức + >= 1 approval hợp lệ
  MAJOR (sửa/xoá/thu hẹp)           -> lock bump MAJOR + >= 3 approval hợp lệ, trong đó >= 1 người thuộc `core` (Lead/Core)

Approval hợp lệ = review APPROVED mới nhất của người có tên trong .github/contract-reviewers.yaml, không phải tác giả PR.
Ngoại lệ: ở MAJOR, tác giả (nếu nằm trong danh sách) được tính là 1 trong 3 người chấp thuận; ở MINOR/PATCH thì không.
Danh sách rỗng => FAIL (fail-closed), không bao giờ mặc định cho qua.

    python tools/contract_check.py --base-ref origin/main --reviews-file reviews.json --author <login>
"""
from __future__ import annotations

import argparse
import json
import pathlib
import sys

import yaml

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import contract_diff  # noqa: E402
import freeze_contract as fc  # noqa: E402

REVIEWERS_FILE = ".github/contract-reviewers.yaml"
MIN_APPROVALS = {contract_diff.NONE: 1, contract_diff.MINOR: 1, contract_diff.MAJOR: 3}


def load_reviews(text: str) -> list[dict]:
    """`gh api --paginate` nối nhiều mảng JSON liền nhau; gộp lại."""
    decoder, pos, out = json.JSONDecoder(), 0, []
    text = text.strip()
    while pos < len(text):
        value, end = decoder.raw_decode(text, pos)
        out.extend(value if isinstance(value, list) else [value])
        pos = end
        while pos < len(text) and text[pos].isspace():
            pos += 1
    return out


def latest_approvals(reviews: list[dict], author: str | None) -> set[str]:
    """Trạng thái mới nhất của mỗi người: chỉ APPROVED mới tính; COMMENTED không huỷ approval cũ, CHANGES_REQUESTED/DISMISSED thì huỷ."""
    state: dict[str, str] = {}
    for review in sorted(reviews, key=lambda r: r.get("submitted_at") or ""):
        login = ((review.get("user") or {}).get("login") or "").lower()
        status = review.get("state")
        if not login or status in (None, "PENDING", "COMMENTED"):
            continue
        state[login] = status
    approved = {login for login, status in state.items() if status == "APPROVED"}
    return approved - {(author or "").lower()}


def load_reviewers(root: pathlib.Path) -> tuple[set[str], set[str]]:
    data = yaml.safe_load((root / REVIEWERS_FILE).read_text(encoding="utf-8")) or {}
    core = {str(x).lower() for x in (data.get("core") or [])}
    others = {str(x).lower() for x in (data.get("reviewers") or [])}
    return core, others | core


def evaluate(root: pathlib.Path, base_ref: str, approvals: set[str], core: set[str], eligible: set[str],
             author: str = "") -> tuple[bool, list[str]]:
    msgs: list[str] = []
    lock, now = fc.read_lock(root), fc.current_hashes(root)
    if any(lock["files"].get(f) != now[f] for f in fc.FILES):
        return False, ["FAIL: file contract lệch CONTRACT.lock (chạy freeze_contract.py --write --version ...)."]

    level, changed = contract_diff.NONE, []
    for f in fc.FILES:
        path = root / f
        new = path.read_bytes().decode("utf-8-sig").replace("\r\n", "\n") if path.is_file() else None
        old = fc.git_show(root, base_ref, f)
        lv, why = contract_diff.classify_file(f, old, new)
        if old != new:
            changed.append(f)
            msgs += why
            if contract_diff._RANK[lv] > contract_diff._RANK[level]:
                level = lv
    if not changed:
        return True, ["PASS: không có file contract nào đổi."]

    base_lock_text = fc.git_show(root, base_ref, fc.LOCK_NAME)
    if base_lock_text is not None:
        got = fc.bump_kind(json.loads(base_lock_text)["version"], lock["version"])
        need = fc._NEED[level]
        if got < need:
            names = {0: "không bump", 1: "patch", 2: "minor", 3: "major"}
            return False, msgs + [f"FAIL: thay đổi mức {level.upper()} cần bump {names[need]}, lock hiện: {names[got]}."]
    else:
        msgs.append("LƯU Ý: base chưa có CONTRACT.lock, bỏ qua kiểm bump.")

    valid = approvals & eligible
    author = author.lower()
    if level == contract_diff.MAJOR and author in eligible:
        valid = valid | {author}  # MAJOR: tác giả tính là 1 trong 3 người chấp thuận (quyết định của chủ repo)
    need_n = MIN_APPROVALS[level]
    if not eligible:
        return False, msgs + [f"FAIL: {REVIEWERS_FILE} chưa có ai (fail-closed) — điền username GitHub của người duyệt."]
    if len(valid) < need_n:
        return False, msgs + [f"FAIL: mức {level.upper()} cần >= {need_n} approval hợp lệ, có {len(valid)} ({', '.join(sorted(valid)) or 'không ai'})."]
    if level == contract_diff.MAJOR and not (valid & core):
        return False, msgs + ["FAIL: thay đổi MAJOR cần ít nhất 1 approval từ nhóm Lead/Core."]
    return True, msgs + [f"PASS: mức {level.upper()}, {len(valid)} approval hợp lệ ({', '.join(sorted(valid))})."]


def main(argv: list[str] | None = None) -> int:
    if sys.stdout and hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--base-ref", required=True)
    ap.add_argument("--reviews-file", required=True)
    ap.add_argument("--author", default="")
    ap.add_argument("--root", default=str(pathlib.Path(__file__).resolve().parent.parent))
    args = ap.parse_args(argv)
    root = pathlib.Path(args.root)
    reviews = load_reviews(pathlib.Path(args.reviews_file).read_text(encoding="utf-8"))
    core, eligible = load_reviewers(root)
    ok, msgs = evaluate(root, args.base_ref, latest_approvals(reviews, args.author), core, eligible, args.author)
    for line in msgs:
        print(line)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
