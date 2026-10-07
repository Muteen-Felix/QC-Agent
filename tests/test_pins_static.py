"""Mọi action trong MỌI workflow và mọi image nền của Dockerfile phải ghim theo SHA/digest (DoD Sprint 4, S4-07). `test_workflow_static.py` chỉ canh hai workflow reusable và
`test_image_workflow_static.py` canh image.yml; file này phủ phần còn lại (ci.yml, contract-check.yml, mọi workflow thêm sau này) và Dockerfile. Không cần Docker/GitHub."""
import re
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parent.parent
WORKFLOWS = sorted((ROOT / ".github" / "workflows").glob("*.y*ml"))
ACTION = re.compile(r"[\w.-]+/[\w./-]+@[0-9a-f]{40}")


def _uses(path: Path) -> list[str]:
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    refs = []
    for job in (data.get("jobs") or {}).values():
        refs += [step["uses"] for step in job.get("steps", []) if "uses" in step]
        if "uses" in job:
            refs.append(job["uses"])
    return [ref for ref in refs if not ref.startswith("./")]


def test_the_scan_sees_every_workflow_and_some_actions():
    assert {p.name for p in WORKFLOWS} >= {"ci.yml", "contract-check.yml", "image.yml", "qc-gate.reusable.yml", "qc-groundtruth.reusable.yml"}
    assert sum(len(_uses(p)) for p in WORKFLOWS) >= 20


@pytest.mark.parametrize("path", WORKFLOWS, ids=lambda p: p.name)
def test_every_action_is_pinned_to_a_40_character_commit_sha(path):
    unpinned = [ref for ref in _uses(path) if not ACTION.fullmatch(ref)]
    assert not unpinned, f"{path.name}: chưa ghim SHA (dùng lại SHA đã có ở ci.yml/image.yml): {sorted(set(unpinned))}"


def test_every_action_sha_is_used_consistently_for_the_same_action():
    """Cùng một action không được có hai SHA khác nhau giữa các workflow (nâng phiên bản phải đổi đồng loạt)."""
    seen: dict[str, set[str]] = {}
    for path in WORKFLOWS:
        for ref in _uses(path):
            name, _, sha = ref.partition("@")
            seen.setdefault(name, set()).add(sha)
    assert not {name: shas for name, shas in seen.items() if len(shas) > 1}, seen


def test_every_base_image_of_the_dockerfile_is_pinned_by_digest():
    text = (ROOT / "Dockerfile").read_text(encoding="utf-8")
    args = dict(re.findall(r"^ARG (\w+_IMAGE)=(\S+)", text, flags=re.M))
    assert {"PYTHON_IMAGE", "NODE_IMAGE", "K6_IMAGE", "UV_IMAGE"} <= set(args)
    for name, ref in args.items():
        assert re.search(r"@sha256:[0-9a-f]{64}$", ref), f"ARG {name} chưa ghim digest: {ref}"
    for ref in re.findall(r"^FROM (\S+)", text, flags=re.M):
        assert ref.startswith("${") or re.search(r"@sha256:[0-9a-f]{64}$", ref), f"FROM {ref} chưa ghim digest"
    assert all(ref.startswith("${") or "@sha256:" in ref for ref in re.findall(r"^FROM (\S+)", text, flags=re.M))
