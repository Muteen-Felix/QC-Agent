"""Rào chắn của pruner trên MỌI ca của cả ba dataset (S4-04): danh sách file luôn đầy đủ, không mất dòng thay đổi thật, floor và FULL SET không phụ thuộc pruner.

Chạy trên repo tạm dựng từ dataset (không mạng, không LLM); mỗi dataset một test nên xdist chia được.
"""
import json
import subprocess
from pathlib import Path

import pytest

from qc_agent.llm.client import ToolCall, Usage
from qc_agent.selector import agent, pruner, rules
from tools import selector_datasets

DATASETS = selector_datasets.names()


def _raw(checkout: Path, base: str, head: str, *specs: str) -> str:
    return subprocess.run(["git", "-c", "core.quotepath=off", "diff", "--no-color", "-w", "-U1", "-M", base, head, "--", *specs], cwd=checkout, check=True,
                          capture_output=True).stdout.decode("utf-8", "replace")


def _changed_lines(diff: str) -> list[str]:
    return [line for line in diff.splitlines() if line.startswith(("+", "-")) and not line.startswith(("+++", "---"))]


def _is_comment_or_blank(line: str, suffix: str) -> bool:
    markers = ("<!--", "-->") if suffix == ".md" else pruner.COMMENT.get(suffix, ())
    text = line[1:].strip()
    return not text or bool(markers and text.startswith(markers))


@pytest.mark.parametrize("name", DATASETS)
def test_every_case_keeps_the_full_file_list_and_every_real_changed_line(name, tmp_path):
    dataset = selector_datasets.load(name)
    checked_code = 0
    for case in dataset.cases():
        checkout = tmp_path / case.name / "sut"
        base, head = dataset.build_repo(case.patch, checkout)
        diff = pruner.prune(checkout, base, head)
        raw_paths = subprocess.run(["git", "-c", "core.quotepath=off", "diff", "--name-only", "-z", "-M", diff.merge_base, head], cwd=checkout, check=True,
                                   capture_output=True).stdout.decode("utf-8").split("\0")
        assert [item.path for item in diff.files] == sorted(path for path in raw_paths if path), case.name                  # danh sách file = diff thô, không thiếu không thừa
        payload_paths = [entry["path"] for entry in json.loads(agent.payload_json(diff.files))]
        assert payload_paths == [item.path for item in diff.files], case.name                                                  # và payload gửi đi cũng vậy
        for item in diff.files:
            if item.kind == "code":
                checked_code += 1
                shown = (item.hunks or "").splitlines()
                suffix = Path(item.path).suffix.lower()
                for line in _changed_lines(_raw(checkout, base, head, *([item.old_path] if item.old_path else []), item.path)):
                    if item.truncated:
                        break   # bị cắt theo trần mỗi file: đã đánh dấu truncated, kiểm riêng ở test diff lớn
                    assert line in shown or _is_comment_or_blank(line, suffix), f"{case.name}: mất dòng thay đổi {line[:60]!r} của {item.path}"
            else:
                assert item.hunks is None, f"{case.name}: {item.path} ({item.kind}) không được mang hunk"                       # generated/vendor/lockfile/binary/deleted chỉ liệt kê
    assert checked_code > 0


@pytest.mark.parametrize("name", DATASETS)
def test_floor_workers_survive_every_selection_and_full_set_ignores_the_llm(name, tmp_path, monkeypatch):
    dataset = selector_datasets.load(name)
    policy, suite_map, module_map = dataset.context()
    monkeypatch.setenv("QC_SELECT_CACHE_DIR", "none")
    for case in dataset.cases()[:6]:                                                        # sáu ca đầu đủ các đường: LLM, floor-only, full set
        checkout = tmp_path / case.name / "sut"
        base, head = dataset.build_repo(case.patch, checkout)
        diff = pruner.prune(checkout, base, head)
        decision = rules.decide([rules.ChangedFile(item.path, item.status) for item in diff.files], policy, module_map, suite_map)

        monkeypatch.setattr(agent, "call_tool", lambda **kwargs: ToolCall({"selections": []}, Usage(), "fake", "tool_use", 0))
        selected = agent.select(diff, decision, policy, suite_map, module_map, egress_dir=tmp_path)
        assert set(policy["floor_workers"]) <= set(selected["workers"]), case.name         # LLM chọn rỗng vẫn có floor security


def test_known_gaps_of_the_default_policy_are_visible_in_monorepo_poly():
    """Phát hiện của S4-04, KHÔNG sửa `_default.yaml` ở đây: lockfile/manifest lồng và Dockerfile tên khác không được rules chọn FULL SET. Sửa glob thì cập nhật test này."""
    dataset = selector_datasets.load("monorepo-poly")
    policy, suite_map, module_map = dataset.context()
    gaps = []
    for case in dataset.cases(include_injections=False):
        paths = [line.split(" b/")[-1] for line in case.patch.read_text(encoding="utf-8").splitlines() if line.startswith("diff --git")]
        decision = rules.decide([rules.ChangedFile(path, "M") for path in paths], policy, module_map, suite_map)
        wants_full = set(case.label["expect_workers"]) >= (set(suite_map) - set(policy["floor_workers"])) and bool(case.label["expect_workers"])
        if wants_full != decision.full_set:
            gaps.append(case.name)
    assert gaps == ["mp-020", "mp-022"]
    assert "gt-functional" not in str(policy["blocking_suites"]) and "pytest" not in suite_map   # pytest không chọn được dưới policy mặc định
