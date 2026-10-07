"""tools/selector_datasets.py (S4-04 mục 1): bộ nạp manifest dùng chung cho eval_selector và eval_cost."""
import json
from pathlib import Path

import pytest
import yaml

from tools import eval_selector, selector_datasets as sd

ROOT = Path(__file__).resolve().parent.parent


def test_default_dataset_is_the_untouched_noteboard_golden_set():
    dataset = sd.load()
    assert dataset.name == "noteboard" == sd.DEFAULT and dataset.labels_status == "reviewed" and dataset.cost_target["median_reduction"] is None
    names = [case.name for case in dataset.cases()]
    assert len(names) == 40 and len([n for n in names if n.startswith("injection-")]) == 10 and len(dataset.cases(include_injections=False)) == 30
    assert dataset.sut == ROOT / "tests/fixtures/sut/noteboard" and dataset.patches == ROOT / "tests/fixtures/diffs"
    assert {case.split for case in dataset.cases()} == {"tune"}   # toàn bộ là tập tune lịch sử
    assert yaml.safe_load((ROOT / "tests/fixtures/diffs/labels.yaml").read_text(encoding="utf-8"))["labeled_by"] == dataset.meta["labeled_by"]


def test_the_default_eval_selector_run_scores_exactly_the_noteboard_samples():
    result = eval_selector.evaluate()
    assert result["samples"] == 40 and result["dataset"] == "noteboard" and result["quality_evidence"] == "fake-pipeline-only" and result["unverified"] is None
    assert [d["name"] for d in result["diffs"]] == [c.name for c in sd.load().cases()]


def test_names_lists_every_manifest_and_unknown_dataset_is_a_manifest_error():
    assert "noteboard" in sd.names()
    with pytest.raises(sd.ManifestError, match="không có dataset"):
        sd.load("khong-co")


def _copy(tmp_path, **changes) -> Path:
    """Sao manifest noteboard sang thư mục tạm (đường dẫn tương đối được đổi thành tuyệt đối) với một vài khoá bị sửa."""
    real = sd.load()
    root = tmp_path / "ds"
    (root / "d").mkdir(parents=True)
    manifest = {"name": "d", "sut": str(real.sut), "patches": str(real.patches), "injection_patches": str(real.injection_patches),
                "labels": str(ROOT / "tests/fixtures/diffs/labels.yaml"), "project": {"slug": "noteboard"}, "labels_status": "reviewed",
                "reviewed_note": "test", "cost_target": {"median_reduction": 0.4}, "splits": {"tune": [], "holdout": ["diff-002"]}}
    manifest.update(changes)
    (root / "d" / "manifest.yaml").write_text(yaml.safe_dump(manifest), encoding="utf-8")
    return root


def test_holdout_cases_are_tagged_and_selectable(tmp_path):
    dataset = sd.load("d", datasets_dir=_copy(tmp_path))
    assert [c.name for c in dataset.cases(split="holdout")] == ["diff-002"] and "diff-002" not in [c.name for c in dataset.cases(split="tune")]


@pytest.mark.parametrize("changes,message", [
    ({"name": "other"}, "trùng tên thư mục"),
    ({"labels_status": "maybe"}, "labels_status"),
    ({"reviewed_note": None}, "reviewed_by"),                                   # reviewed mà không có người duyệt cũng không có ghi chú nguồn
    ({"cost_target": {"median_reduction": None}}, "reason"),                    # cost_target null phải kèm số đo làm lý do
    ({"cost_target": {"median_reduction": 1.5}}, "median_reduction"),
    ({"splits": {"holdout": ["khong-co-ca-nay"]}}, "holdout"),
    ({"patches": "/khong/co/thu/muc"}, "patches"),
])
def test_bad_manifests_are_reported_clearly(tmp_path, changes, message):
    with pytest.raises(sd.ManifestError, match=message):
        sd.load("d", datasets_dir=_copy(tmp_path, **changes))


def test_unreviewed_labels_are_flagged_and_reviewed_by_forces_the_reviewed_status(tmp_path):
    unreviewed = sd.load("d", datasets_dir=_copy(tmp_path, labels_status="unreviewed", reviewed_note=None))
    assert "CHƯA KIỂM CHỨNG" in sd.unverified_banner(unreviewed) and sd.unverified_banner(sd.load()) is None
    root = tmp_path / "x"
    root.mkdir()
    labels = yaml.safe_load((ROOT / "tests/fixtures/diffs/labels.yaml").read_text(encoding="utf-8"))
    labels["reviewed_by"] = "qa-lead"
    (root / "labels.yaml").write_text(yaml.safe_dump(labels), encoding="utf-8")
    base = _copy(tmp_path / "y", labels_status="unreviewed", reviewed_note=None, labels=str(root / "labels.yaml"))
    with pytest.raises(sd.ManifestError, match="reviewed_by thì labels_status phải là reviewed"):
        sd.load("d", datasets_dir=base)


def test_unreviewed_dataset_scores_run_but_carry_the_unverified_banner(tmp_path):
    dataset = sd.load("d", datasets_dir=_copy(tmp_path, labels_status="unreviewed", reviewed_note=None))
    result = eval_selector.score(eval_selector.collect(dataset=dataset, only=("diff-001", "diff-011")))
    assert result["labels_status"] == "unreviewed" and result["unverified"].startswith("CHƯA KIỂM CHỨNG")


def test_compare_only_means_something_between_two_real_runs():
    fake = {"llm": "fake", "llm_recall": 1.0, "llm_precision": 1.0, "final_recall": 1.0, "critical_recall": 1.0}
    real = {"llm": "real", "llm_recall": .9706, "llm_precision": .9167, "final_recall": 1.0, "critical_recall": 1.0, "model": ["m"]}
    assert eval_selector.compare(fake, real)["delta_pp"] is None and eval_selector.compare(real, fake)["delta_pp"] is None
    after = {**real, "llm_recall": .9412, "llm_precision": .9444}
    assert eval_selector.compare(after, real)["delta_pp"] == {"llm_recall": -2.9, "llm_precision": 2.8, "final_recall": 0.0, "critical_recall": 0.0}


def test_cli_dataset_flag_reports_a_missing_dataset_as_a_system_error_and_all_reports_each_dataset(tmp_path, capsys):
    assert eval_selector.main(["--dataset", "khong-co"]) == 3
    out = tmp_path / "all.json"
    assert eval_selector.main(["--dataset", "all", "--out-json", str(out)]) == 1                       # monorepo-poly: rules lệch nhãn nháp ở 2 ca (phát hiện về _default.yaml) nên không "passed"
    results = json.loads(out.read_text(encoding="utf-8"))["datasets"]
    assert set(results) == set(sd.names())                                                          # báo RIÊNG từng dataset
    assert (results["noteboard"]["passed"], results["node-api"]["passed"], results["monorepo-poly"]["passed"]) == (True, True, False)
    assert results["monorepo-poly"]["rules_full_set"] == {"ok": 22, "total": 24} and results["monorepo-poly"]["unverified"].startswith("CHƯA KIỂM CHỨNG")
    assert all(item["quality_evidence"] == "fake-pipeline-only" for item in results.values())


def test_selector_source_never_names_a_dataset_or_fixture_repo():
    """Tín hiệu generated/vendor phải là quy ước chung: không hard-code tên dataset hay thư mục riêng của fixture vào mã selector."""
    banned = ["noteboard", "toyapp", *[n for n in sd.names() if n != "noteboard"]]
    hits = []
    for path in (ROOT / "src/qc_agent/selector").glob("*.py"):
        text = path.read_text(encoding="utf-8")
        hits += [f"{path.name}:{word}" for word in banned if word in text]
    assert hits == []
