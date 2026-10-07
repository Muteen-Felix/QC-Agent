"""Fixture `tests/fixtures/sut/node-api-harness` (S4-05): kiểm bằng policy mặc định thật, rules của selector thật và worker coverage-debt thật, KHÔNG cần Docker.
Các PR mẫu N1–N4 nằm ở tests/harness_kit.py; ở đây khoá cái mà từng PR được phép/không được phép chạm vào (đặc biệt N3 không đụng `.qc-agent/**`)."""
import json
import re
import shutil
from pathlib import Path

import pytest
import yaml

from qc_agent import settings
from qc_agent.adapters import coverage_debt_worker as debt
from qc_agent.core import project as pj, registry
from qc_agent.selector import rules
from tests import harness_kit as kit

SUT = kit.NODE_SUT
ROOT = kit.ROOT
SLUG = kit.NODE_PROJECT


@pytest.fixture
def default_only(tmp_path):
    """Policy dir chỉ chứa bản chép của `_default.yaml`: chứng minh fixture đi đường mặc định, không lỡ dùng noteboard.yaml."""
    target = tmp_path / "policy"
    target.mkdir()
    shutil.copy(kit.POLICY_DIR / "_default.yaml", target / "_default.yaml")
    return target


@pytest.fixture
def cfg(default_only):
    config, info = pj.resolve_project(SLUG, default_only)
    assert info["source"] == "default" and list(info["files"]) == ["_default.yaml"], info   # không có file riêng cho slug này
    return config


def _repo(tmp_path, *, control=False):
    repo = tmp_path / "repo"
    shutil.copytree(SUT, repo)
    kit.git(repo, "init", "-q")
    if control:
        kit.strip_exclude_path(repo)
    return repo, kit.commit(repo, "base")


def _changed(repo: Path, base: str) -> list[str]:
    return sorted(kit.git(repo, "diff", "--name-only", base, "HEAD").splitlines())


# ---- cấu hình tương thích policy mặc định ----

def test_the_slug_is_unregistered_so_the_default_policy_applies():
    assert not (kit.POLICY_DIR / f"{SLUG}.yaml").exists()


def test_suites_satisfy_the_default_policy_and_map_to_the_expected_workers(cfg):
    """Suite blocking vắng là PlanError (core/project.py); fixture phải có đủ api-contract, sast, secrets, deps (+ coverage-debt cho advisory)."""
    suites = pj.load_suites(SUT / cfg["suites_dir"])
    assert sorted(suites) == ["api-contract", "coverage-debt", "deps", "sast", "secrets"]
    workers = registry.load_many(settings.get().workers_dirs)
    assert pj.suites_by_worker(cfg, "pr", suites, workers) == {"coverage-debt": ["coverage-debt"], "gitleaks": ["secrets"], "schemathesis": ["api-contract"],
                                                                "semgrep": ["sast"], "trivy": ["deps"]}
    plan, _ = pj.build_plan(cfg, "pr", suites)
    assert sorted(t["capability"] for t in plan["tasks"]) == ["api.property", "code.sast", "code.secret", "deps.vuln", "repo.coverage_debt"]


def test_default_policy_has_no_jira_and_no_functional_suite(cfg):
    """Điều harness KHÔNG được kỳ vọng ở fixture này (xem R4): không ticket Jira, không pytest/gt-functional."""
    assert "jira" not in cfg and "gt-functional" not in cfg["modes"]["pr"]["blocking_suites"]
    assert cfg["modes"]["pr"]["floor_workers"] == ["gitleaks", "semgrep"]


def test_module_map_is_approved_and_every_module_suite_exists(cfg):
    module_map = yaml.safe_load((SUT / ".qc-agent" / "ground-truth" / "module-map.yaml").read_text(encoding="utf-8"))
    suites = pj.load_suites(SUT / cfg["suites_dir"])
    assert module_map["status"] == "approved" and module_map["modules"]
    assert all(s in suites for module in module_map["modules"] for s in module["suites"])


def test_dockerfile_uses_the_same_node_digest_as_the_product_dockerfile():
    product = re.search(r"ARG NODE_IMAGE=(\S+)", (ROOT / "Dockerfile").read_text(encoding="utf-8")).group(1)
    fixture = re.search(r"^FROM (\S+)", (SUT / "Dockerfile").read_text(encoding="utf-8"), re.M).group(1)
    assert fixture == product and "@sha256:" in fixture


def test_lockfile_has_a_production_dependency_trivy_can_scan():
    """Spike S2: lockfile không dependency => Trivy `Results` rỗng => `deps` error => gate đỏ; dependency `dev` bị Trivy bỏ qua."""
    lock = json.loads((SUT / "package-lock.json").read_text(encoding="utf-8"))
    package = json.loads((SUT / "package.json").read_text(encoding="utf-8"))
    prod = {name: meta for name, meta in lock["packages"].items() if name and not meta.get("dev")}
    assert prod, "cần ≥ 1 package prod trong lockfile"
    assert "left-pad" in package["dependencies"] and {n.split("node_modules/")[-1] for n in prod} <= set(package["dependencies"])
    assert "devDependencies" not in package


def test_the_fixture_has_no_dockerfile_step_that_needs_the_network():
    instructions = [line.split()[0].upper() for line in (SUT / "Dockerfile").read_text(encoding="utf-8").splitlines() if line.strip() and not line.startswith("#")]
    assert "RUN" not in instructions     # không build, không tải gói (hiệu chỉnh #16: TLS ở mạng công ty)


def test_base_spec_has_no_profile_operation_but_the_suite_already_reserves_its_exclude_path():
    spec = json.loads((SUT / "openapi.json").read_text(encoding="utf-8"))
    assert sorted(spec["paths"]) == ["/health", "/users/{id}"]
    suite = yaml.safe_load((SUT / ".qc-agent" / "suites" / "api-contract.yaml").read_text(encoding="utf-8"))
    assert suite["tasks"][0]["inputs"]["exclude_path"] == [kit.PROFILE_PATH]


# ---- PR nào chạm vào cái gì ----

@pytest.mark.parametrize(("apply", "expected"), [
    (kit.apply_n1, ["openapi.json", "src/routes/users.ts"]),
    (kit.apply_n2, ["src/routes/debug.ts"]),
    (kit.apply_n3, ["openapi.json", "src/routes/profile.ts", "src/server.ts"]),
    (kit.apply_n4, ["package-lock.json"]),
])
def test_each_sample_pr_touches_exactly_its_files(tmp_path, apply, expected):
    repo, base = _repo(tmp_path)
    apply(repo)
    kit.commit(repo, "head")
    assert _changed(repo, base) == expected


def test_n3_does_not_touch_qc_agent_so_it_is_not_a_full_set(tmp_path, cfg):
    """Review: `.qc-agent/**` nằm trong full_set_paths của `_default.yaml`; sửa suite làm N3 thành FULL SET và kéo `deps` (phụ thuộc tuổi DB Trivy) vào."""
    repo, base = _repo(tmp_path)
    kit.apply_n3(repo)
    kit.commit(repo, "head")
    changed = _changed(repo, base)
    assert not any(path.startswith(".qc-agent/") for path in changed)
    policy = cfg["modes"]["pr"]
    decision = rules.decide([rules.ChangedFile(path, "M") for path in changed], policy, None, {})
    assert not decision.full_set and decision.reason == "analysis"
    # đối chứng: chính phiên bản rev.2 (PR sửa suite) là FULL SET
    assert rules.decide([rules.ChangedFile(".qc-agent/suites/api-contract.yaml", "M")], policy, None, {}).full_set


@pytest.mark.parametrize(("path", "full"), [("package-lock.json", True), ("package.json", True), ("Dockerfile", True), (".qc-agent/suites/deps.yaml", True),
                                            ("openapi.json", False), ("src/server.ts", False), ("src/routes/debug.ts", False)])
def test_full_set_classification_of_the_paths_the_scenarios_use(cfg, path, full):
    assert rules.decide([rules.ChangedFile(path, "M")], cfg["modes"]["pr"], None, {}).full_set is full


def test_module_map_hints_schemathesis_for_the_route_prs(cfg):
    module_map = yaml.safe_load((SUT / ".qc-agent" / "ground-truth" / "module-map.yaml").read_text(encoding="utf-8"))
    suites = pj.load_suites(SUT / cfg["suites_dir"])
    suite_map = pj.suites_by_worker(cfg, "pr", suites, registry.load_many(settings.get().workers_dirs))
    decision = rules.decide([rules.ChangedFile("src/routes/users.ts", "M")], cfg["modes"]["pr"], module_map, suite_map)
    assert set(decision.hint_workers) == {"schemathesis"} and not decision.full_set and not decision.floor_only


# ---- N3 ở mức worker coverage-debt (đối chứng nhanh, không Docker) ----

def test_n3_with_the_reserved_exclude_path_is_exactly_one_low_debt(tmp_path):
    repo, base = _repo(tmp_path)
    kit.apply_n3(repo)
    kit.commit(repo, "head")
    result = debt.scan(repo, base)
    assert result["status"] == "ok" and result["metrics"]["debt.new"] == 1
    assert [f["finding_id"] for f in result["findings"]] == ["debt:api_contract:GET /users/{id}/profile"]
    assert result["findings"][0]["path"] == "openapi.json" and isinstance(result["findings"][0]["line"], int)


def test_n3_control_without_exclude_path_is_no_debt_because_schemathesis_covers_it(tmp_path):
    """Spike S7: `CoverageIndex.covered` coi operation là đã có test khi path có trong openapi.json và còn task api.property không loại nó."""
    repo, base = _repo(tmp_path, control=True)
    kit.apply_n3(repo)
    kit.commit(repo, "head")
    result = debt.scan(repo, base)
    assert result["status"] == "ok" and result["metrics"]["debt.new"] == 0 and result["findings"] == []


@pytest.mark.parametrize("apply", [kit.apply_n1, kit.apply_n2, kit.apply_n4])
def test_other_prs_open_no_test_debt(tmp_path, apply):
    repo, base = _repo(tmp_path)
    apply(repo)
    kit.commit(repo, "head")
    assert debt.scan(repo, base)["metrics"]["debt.new"] == 0


def test_the_control_edit_only_removes_the_exclude_block(tmp_path):
    repo, _ = _repo(tmp_path, control=True)
    suite = yaml.safe_load((repo / ".qc-agent" / "suites" / "api-contract.yaml").read_text(encoding="utf-8"))
    assert "exclude_path" not in suite["tasks"][0]["inputs"] and suite["tasks"][0]["inputs"]["max_examples"] == 25 and suite["tasks"][0]["oracle"]


def test_n3_openapi_is_valid_json_with_the_profile_operation(tmp_path):
    repo, _ = _repo(tmp_path)
    kit.apply_n3(repo)
    spec = json.loads((repo / "openapi.json").read_text(encoding="utf-8"))
    assert sorted(spec["paths"]) == ["/health", "/users/{id}", kit.PROFILE_PATH]
    assert {"200", "404"} <= set(spec["paths"][kit.PROFILE_PATH]["get"]["responses"])


def test_workspace_helpers_never_commit_the_gate_output(tmp_path):
    repo, base = _repo(tmp_path)
    (repo / "runs" / "r-0001").mkdir(parents=True)
    (repo / "runs" / "r-0001" / "report.md").write_text("x", encoding="utf-8")
    (repo / "marker.txt").write_text("y", encoding="utf-8")
    kit.commit(repo, "head")
    assert _changed(repo, base) == ["marker.txt"]
