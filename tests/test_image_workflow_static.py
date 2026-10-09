"""Kiểm tĩnh .github/workflows/image.yml (S4-07): action ghim SHA, và Job Summary in digest kèm lệnh ghim cho CẢ HAI workflow gọi của repo SUT.
Không cần Docker/GitHub: bước "Digest" được chạy thật bằng bash với `GITHUB_STEP_SUMMARY` là file tạm, rồi lệnh `sed` in ra được chạy trên bản chép của hai template caller
(`qc-agent init` sinh chúng) để chứng minh nó đổi đúng một dòng `image:` mỗi file và không đụng `sut_db_image`.
Giới hạn: đây KHÔNG chạy workflow trên GitHub; nội dung Job Summary thật chỉ thấy khi merge vào main (chưa kiểm chứng)."""
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))
import run_reusable_locally as harness  # noqa: E402

WORKFLOW = ROOT / ".github" / "workflows" / "image.yml"
TEXT = WORKFLOW.read_text(encoding="utf-8")
DATA = yaml.safe_load(TEXT)
STEPS = DATA["jobs"]["build"]["steps"]
DIGEST = "sha256:" + "ab" * 32
REF = f"ghcr.io/muteen-felix/qc-agent@{DIGEST}"


def _step(name_prefix: str) -> dict:
    return next(step for step in STEPS if str(step.get("name", "")).startswith(name_prefix))


def test_every_action_is_pinned_to_a_40_character_commit_sha():
    uses = [step["uses"] for step in STEPS if "uses" in step]
    assert uses, "không thấy action nào: bộ quét hỏng"
    for ref in uses:
        assert re.fullmatch(r"[\w.-]+/[\w./-]+@[0-9a-f]{40}", ref), f"{ref} chưa ghim theo SHA"


def test_the_digest_step_runs_only_when_publishing_and_uses_the_digest_output_of_the_build():
    step = _step("Digest")
    assert step["env"]["REF"] == "${{ env.IMAGE }}@${{ steps.build.outputs.digest }}"
    assert step["if"] == "github.event_name != 'pull_request'"


def _run_summary_step(tmp_path: Path) -> str:
    summary = tmp_path / "summary.md"
    summary.write_text("", encoding="utf-8")
    done = subprocess.run([harness.find_bash(), "-e", "-c", _step("Digest")["run"]], cwd=tmp_path, capture_output=True, text=True, encoding="utf-8", timeout=60,
                          env={**os.environ, "REF": REF, "GITHUB_STEP_SUMMARY": summary.as_posix(), "MSYS_NO_PATHCONV": "1"})
    assert done.returncode == 0, done.stderr
    return summary.read_text(encoding="utf-8")


def test_the_summary_prints_the_digest_and_names_both_caller_workflows(tmp_path):
    from qc_agent.scaffold import init, templates
    text = _run_summary_step(tmp_path)
    assert f"```\n{REF}\n```" in text, "digest phải nằm riêng trong một khối mã để sao chép"
    assert f"`{templates.GATE_WORKFLOW}`" in text and f"`{init.GT_WORKFLOW}`" in text       # đúng tên file mà `qc-agent init` sinh
    assert f"`qc-agent init --image {REF}`" in text and "digest" in text and "không theo tag" in text
    assert not re.search(r"qc-agent[:@][a-z0-9.-]+(?<!sha256)\b(?!:)", text.replace(REF, "")), "gợi ý không được dẫn tới tag"


def test_the_init_flag_the_summary_recommends_exists():
    assert '"--image"' in (ROOT / "src" / "qc_agent" / "scaffold" / "init.py").read_text(encoding="utf-8")


def test_the_printed_sed_command_pins_exactly_one_image_line_in_each_generated_caller(tmp_path):
    """Chạy đúng lệnh mà người dùng sẽ sao chép, trên bản chép của hai template caller (placeholder `{{image}}` đổi thành một tag cũ)."""
    from qc_agent.scaffold import init, templates
    text = _run_summary_step(tmp_path)
    command = next(line for line in text.splitlines() if line.startswith("sed -i "))
    sut = tmp_path / "sut"
    for target, template in ((templates.GATE_WORKFLOW, "qc-gate.yml.tmpl"), (init.GT_WORKFLOW, "qc-groundtruth.yml.tmpl")):
        path = sut / target
        path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy(ROOT / "src" / "qc_agent" / "scaffold" / "tmpl" / template, path)
        path.write_text(path.read_text(encoding="utf-8").replace("{{image}}", "ghcr.io/muteen-felix/qc-agent:main"), encoding="utf-8", newline="\n")
    before = {target: (sut / target).read_text(encoding="utf-8").splitlines() for target in (templates.GATE_WORKFLOW, init.GT_WORKFLOW)}
    done = subprocess.run([harness.find_bash(), "-e", "-c", command], cwd=sut, capture_output=True, text=True, encoding="utf-8", timeout=60, env={**os.environ, "MSYS_NO_PATHCONV": "1"})
    assert done.returncode == 0, done.stderr
    for target, old in before.items():
        new = (sut / target).read_text(encoding="utf-8").splitlines()
        changed = [(a, b) for a, b in zip(old, new) if a != b]
        assert len(old) == len(new) and len(changed) == 1, (target, changed)
        assert changed[0][0].strip() == "image: ghcr.io/muteen-felix/qc-agent:main" and changed[0][1].strip() == f"image: {REF}", changed
        assert not any("sut_db_image" in b for a, b in zip(old, new) if a != b)


def test_the_sed_pattern_would_not_touch_a_db_image_line():
    """Chốt rủi ro: `sut_db_image: postgres@sha256:...` không được bị ghi đè bằng digest của qc-agent."""
    sample = "      image: old\n      sut_db_image: postgres@sha256:" + "0" * 64 + "\n"
    out = re.sub(r"(?m)^( *image: ).*", lambda m: m.group(1) + REF, sample)
    assert out.splitlines()[0].endswith(REF) and out.splitlines()[1] == sample.splitlines()[1]


@pytest.mark.parametrize("trigger", ["pull_request", "push", "workflow_dispatch"])
def test_the_workflow_still_builds_on_the_same_triggers(trigger):
    assert trigger in DATA[True]


def test_the_two_caller_names_agree_between_the_summary_the_operations_doc_the_templates_and_validate():
    """Tên caller chỉ có MỘT nguồn (hằng số của scaffold). Sai tên (vd. `qc.yml` thay vì `qc-gate.yml`) làm lệnh `sed` trong Summary báo không thấy file."""
    from qc_agent.scaffold import init, templates, validate
    gate, gt = templates.GATE_WORKFLOW, init.GT_WORKFLOW
    assert (gate, gt) == (".github/workflows/qc-gate.yml", ".github/workflows/qc-groundtruth.yml") and validate.GT_WORKFLOW == gt
    for name in (gate, gt):
        assert f"`{name}`" in (ROOT / "docs" / "operations.md").read_text(encoding="utf-8"), name
    step = _step("Digest")["run"]
    assert "gate=" + gate in step and "gt=" + gt in step, "image.yml phải dùng đúng hai tên này"
    for template in ("qc-gate.yml.tmpl", "qc-groundtruth.yml.tmpl"):
        assert (ROOT / "src" / "qc_agent" / "scaffold" / "tmpl" / template).is_file()
    legacy = templates.LEGACY_WORKFLOW
    doc = (ROOT / "docs" / "operations.md").read_text(encoding="utf-8")
    for line in doc.splitlines():
        if "`qc.yml`" in line:       # tên cũ chỉ được nhắc kèm lời giải thích là tên cũ
            assert "tên cũ" in line and legacy.endswith("qc.yml"), line
