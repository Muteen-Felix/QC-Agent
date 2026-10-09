"""Bộ nạp dataset dùng chung cho `eval_selector.py` và `eval_cost.py` (S4-04 mục 1).

Một dataset = SUT nhỏ + thư mục patch + nhãn + policy, khai trong `tests/fixtures/selector-datasets/<tên>/manifest.yaml`. Manifest chỉ TRỎ tới file; nhãn do người duyệt đặt
(`labels_status: reviewed` khi `reviewed_by` có mặt), không bao giờ lấy từ output của Selector. Slug chưa đăng ký trong `configs/projects/` nhận `_default.yaml` thật.
"""
from __future__ import annotations

import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
DATASETS_DIR = ROOT / "tests" / "fixtures" / "selector-datasets"
DEFAULT = "noteboard"
STATUSES = ("reviewed", "unreviewed")
_IDENTITY = ["-c", "user.name=QC", "-c", "user.email=qc@example.invalid"]


class ManifestError(Exception):
    """Manifest hoặc dataset sai (thiếu file, khoá lạ, split không khớp nhãn): lỗi hệ thống, không phải kết quả đo."""


@dataclass(frozen=True)
class Case:
    name: str
    label: dict
    patch: Path
    injected: bool
    split: str   # tune | holdout


@dataclass(frozen=True)
class Dataset:
    name: str
    sut: Path
    patches: Path
    injection_patches: Path | None
    labels: dict
    labels_status: str
    project_slug: str
    cost_target: dict
    splits: dict[str, tuple[str, ...]]
    root: Path
    _cache: dict = field(default_factory=dict, compare=False, hash=False, repr=False)

    def cases(self, *, split: str | None = None, include_injections: bool = True) -> list[Case]:
        out = []
        for name, label in self.labels.items():
            if name == "labeled_by" or name == "reviewed_by":
                continue
            injected = name.startswith("injection-")
            if injected and not include_injections:
                continue
            directory = self.injection_patches if injected else self.patches
            patch = (directory or self.patches) / f"{name}.patch"
            if not patch.is_file():
                raise ManifestError(f"{self.name}: thiếu patch {name}")
            case_split = "holdout" if name in self.splits["holdout"] else "tune"
            if split is None or split == case_split:
                out.append(Case(name, label, patch, injected, case_split))
        return out

    @property
    def meta(self) -> dict:
        return {"dataset": self.name, "labels_status": self.labels_status, "cost_target": self.cost_target,
                "labeled_by": self.labels.get("labeled_by"), "reviewed_by": self.labels.get("reviewed_by")}

    def context(self) -> tuple[dict, dict, dict | None]:
        """(policy mode `pr`, suite_map, module_map): dựng một lần cho cả dataset."""
        if "context" not in self._cache:
            from qc_agent.core import project as project_lib, registry
            cfg = project_lib.load_project(self.project_slug, ROOT / "configs" / "projects")
            suites = project_lib.load_suites(self.sut / cfg["suites_dir"])
            suite_map = project_lib.suites_by_worker(cfg, "pr", suites, registry.load(ROOT / "workers"))
            module_path = self.sut / ".qc-agent" / "ground-truth" / "module-map.yaml"
            module_map = yaml.safe_load(module_path.read_text(encoding="utf-8")) if module_path.is_file() else None
            self._cache["context"] = (cfg["modes"]["pr"], suite_map, module_map)
        return self._cache["context"]

    def build_repo(self, patch: Path, destination: Path) -> tuple[str, str]:
        """Repo git tạm = SUT + một commit `base`, rồi áp patch thành commit `change`. Trả về (base, head)."""
        shutil.copytree(self.sut, destination)
        _git(destination, "init", "-q")
        _git(destination, *_IDENTITY, "add", "-A")
        _git(destination, *_IDENTITY, "commit", "-qm", "base")
        base = _git(destination, "rev-parse", "HEAD")
        _git(destination, "apply", str(patch))
        _git(destination, *_IDENTITY, "add", "-A")
        _git(destination, *_IDENTITY, "commit", "-qm", "change")
        return base, _git(destination, "rev-parse", "HEAD")


def _git(repo: Path, *args: str) -> str:
    proc = subprocess.run(["git", *args], cwd=repo, capture_output=True, text=True, timeout=60, check=False)
    if proc.returncode:
        raise RuntimeError("git khong thanh cong: " + " ".join(a for a in args if not a.startswith("user.")) [:60])
    return proc.stdout.strip()


def names() -> list[str]:
    return sorted(path.parent.name for path in DATASETS_DIR.glob("*/manifest.yaml"))


def _path(root: Path, raw, key: str) -> Path:
    if not isinstance(raw, str) or not raw:
        raise ManifestError(f"{root.name}: thiếu khoá {key}")
    return (root / raw).resolve()


def load(name: str = DEFAULT, *, datasets_dir: Path = DATASETS_DIR) -> Dataset:
    root = datasets_dir / name
    manifest_path = root / "manifest.yaml"
    if not manifest_path.is_file():
        raise ManifestError(f"không có dataset {name!r} (có: {', '.join(names()) or '—'})")
    try:
        manifest = yaml.safe_load(manifest_path.read_text(encoding="utf-8"))
    except yaml.YAMLError as error:
        raise ManifestError(f"{name}: manifest không đọc được ({type(error).__name__})") from None
    if not isinstance(manifest, dict) or manifest.get("name") != name:
        raise ManifestError(f"{name}: `name` trong manifest phải trùng tên thư mục")
    sut, patches = _path(root, manifest.get("sut"), "sut"), _path(root, manifest.get("patches"), "patches")
    labels_file = _path(root, manifest.get("labels"), "labels")
    injection = _path(root, manifest["injection_patches"], "injection_patches") if manifest.get("injection_patches") else None
    for label, path in (("sut", sut), ("patches", patches), ("labels", labels_file)):
        if not path.exists():
            raise ManifestError(f"{name}: {label} không tồn tại ({path.relative_to(ROOT) if path.is_relative_to(ROOT) else path})")
    status = manifest.get("labels_status")
    if status not in STATUSES:
        raise ManifestError(f"{name}: labels_status phải là một trong {STATUSES}")
    labels = yaml.safe_load(labels_file.read_text(encoding="utf-8"))
    if not isinstance(labels, dict) or not labels.get("labeled_by"):
        raise ManifestError(f"{name}: file nhãn phải có labeled_by")
    if status == "reviewed" and not (labels.get("reviewed_by") or manifest.get("reviewed_note")):
        raise ManifestError(f"{name}: labels_status reviewed cần reviewed_by trong file nhãn (hoặc reviewed_note giải thích nguồn)")
    if status == "unreviewed" and labels.get("reviewed_by"):
        raise ManifestError(f"{name}: có reviewed_by thì labels_status phải là reviewed")
    target = manifest.get("cost_target") or {}
    reduction = target.get("median_reduction")
    if reduction is not None and not (isinstance(reduction, (int, float)) and 0 < reduction < 1):
        raise ManifestError(f"{name}: cost_target.median_reduction phải là null hoặc số trong (0, 1)")
    if reduction is None and not target.get("reason"):
        raise ManifestError(f"{name}: cost_target null phải kèm reason (số đo)")
    splits = manifest.get("splits") or {}
    holdout = tuple(splits.get("holdout") or ())
    known = {key for key in labels if key not in ("labeled_by", "reviewed_by")}
    if set(holdout) - known:
        raise ManifestError(f"{name}: holdout chứa ca không có nhãn: {sorted(set(holdout) - known)}")
    dataset = Dataset(name, sut, patches, injection, labels, status, str((manifest.get("project") or {}).get("slug") or name),
                      {"median_reduction": reduction, "reason": target.get("reason")}, {"tune": tuple(splits.get("tune") or ()), "holdout": holdout}, root)
    dataset.cases()   # kiểm đủ patch cho mọi nhãn ngay khi nạp
    return dataset


def unverified_banner(dataset: Dataset) -> str | None:
    """Nhãn chưa có người duyệt: mọi số recall/precision/rules_full_set của dataset này không được coi là đạt."""
    return None if dataset.labels_status == "reviewed" else f"CHƯA KIỂM CHỨNG: nhãn của dataset {dataset.name} chưa có người duyệt (labels_status: unreviewed)"
