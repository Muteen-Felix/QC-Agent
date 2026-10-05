"""P2-0 spike (thiết kế gốc docs/phase2/plan-debt.md đã xóa, xem `git log -- docs/phase2`): kiểm chứng hai giả định kỹ thuật TRƯỚC khi sửa core.

  A. PR checkout với fetch-depth 2 là một merge commit: `HEAD^1` luôn là tip nhánh đích, và
     `git diff --name-status -M HEAD^1 HEAD` bóc đúng file thêm (A) / đổi tên (R) của riêng PR.
  B. Env `QC_DIFF_BASE` đi được từ tiến trình gọi -> module adapter (core/runner._spawn) -> tiến trình worker
     (Adapter._exec). Cả hai chặng đều là `{**os.environ, ...}`: không có allowlist nào chặn biến này.

Không sửa src/. Repo git dựng bằng git thật trong tmp_path (không đụng repo đang làm việc); cuối phiên in báo cáo ngắn."""
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from qc_agent.core import runner

pytestmark = pytest.mark.skipif(shutil.which("git") is None, reason="cần binary git")

_REPORT: list[str] = []
_REPORT_SEEN: set[str] = set()


@pytest.fixture(scope="module", autouse=True)
def _print_report(request):
    """In báo cáo ra terminal kể cả khi `pytest -q` (không cần -s)."""
    yield
    tr = request.config.pluginmanager.get_plugin("terminalreporter")
    if tr is None or not _REPORT:
        return
    tr.write_line("")
    tr.write_line("=== P2-0 SPIKE: báo cáo kiểm chứng giả định ===", bold=True)
    for line in _REPORT:
        tr.write_line("  " + line)


def _ok(msg: str) -> None:
    if msg not in _REPORT_SEEN:  # test parametrize cùng một kết luận: chỉ in một lần
        _REPORT_SEEN.add(msg)
        _REPORT.append("[KHỚP] " + msg)


# ───────────────────────── A. Git: merge commit + fetch-depth 2 ─────────────────────────

_GIT_ENV = {  # cách ly khỏi ~/.gitconfig của người chạy (gpgsign, autocrlf, diff.renames=false, ...)
    "GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_NOSYSTEM": "1",
    "GIT_AUTHOR_NAME": "spike", "GIT_AUTHOR_EMAIL": "spike@example.invalid",
    "GIT_COMMITTER_NAME": "spike", "GIT_COMMITTER_EMAIL": "spike@example.invalid",
}

_BODY = "".join(f"line {i}: nội dung đủ dài để git nhận ra đây là cùng một file\n" for i in range(30))


def git(cwd: Path, *args: str) -> str:
    p = subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, encoding="utf-8",
                       env={**os.environ, **_GIT_ENV})
    assert p.returncode == 0, f"git {' '.join(args)} -> {p.returncode}\n{p.stderr}"
    return p.stdout.strip()


def write(repo: Path, rel: str, text: str) -> None:
    f = repo / rel
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text(text, encoding="utf-8", newline="\n")


def commit(repo: Path, msg: str) -> str:
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", msg)
    return git(repo, "rev-parse", "HEAD")


def build_origin(tmp_path: Path, target_advanced: bool) -> dict:
    """Dựng 'origin' giống refs/pull/N/merge của GitHub: nhánh `pr-merge` = merge --no-ff (PR head vào tip nhánh đích).
    target_advanced=True: nhánh đích đã đi tiếp SAU khi PR rẽ nhánh (trường hợp thật, PR không được rebase)."""
    origin = tmp_path / "origin"
    origin.mkdir()
    git(origin, "init", "-q", "-b", "main")
    write(origin, "app/keep.py", _BODY)
    write(origin, "app/old_name.py", _BODY + "# chỉ có ở file cũ\n")
    write(origin, "app/gone.py", _BODY + "# sẽ bị xoá\n")
    fork_point = commit(origin, "base")

    git(origin, "checkout", "-q", "-b", "feature")
    write(origin, "app/routers/new_endpoint.py", "from fastapi import APIRouter\nrouter = APIRouter()\n")  # A
    git(origin, "mv", "app/old_name.py", "app/new_name.py")                                                  # R100
    write(origin, "app/keep.py", _BODY + "# sửa nội dung\n")                                                  # M
    git(origin, "rm", "-q", "app/gone.py")                                                                   # D
    pr_head = commit(origin, "feature: A + R + M + D")

    git(origin, "checkout", "-q", "main")
    if target_advanced:
        write(origin, "app/main_only.py", "# thay đổi của nhánh đích, KHÔNG thuộc PR\n")
        commit(origin, "main tiến thêm")
    base = git(origin, "rev-parse", "main")

    git(origin, "checkout", "-q", "-b", "pr-merge")
    git(origin, "merge", "-q", "--no-ff", "-m", "Merge pull request", "feature")
    merge = git(origin, "rev-parse", "HEAD")
    return {"url": origin.as_uri(), "fork_point": fork_point, "base": base, "pr_head": pr_head, "merge": merge}


def shallow_checkout(tmp_path: Path, o: dict, depth: int) -> Path:
    """Giả lập `actions/checkout` với fetch-depth=<depth> ref merge của PR."""
    dst = tmp_path / f"runner-depth{depth}"
    p = subprocess.run(["git", "clone", "-q", "--depth", str(depth), "--branch", "pr-merge", o["url"], str(dst)],
                       capture_output=True, text=True, env={**os.environ, **_GIT_ENV})
    assert p.returncode == 0, p.stderr
    return dst


def parse_name_status(out: str) -> dict[str, list[tuple]]:
    """`A\\tpath` | `M\\tpath` | `D\\tpath` | `R<score>\\told\\tnew` -> {mã: [(path...)]}."""
    res: dict[str, list[tuple]] = {}
    for line in out.splitlines():
        code, *paths = line.split("\t")
        res.setdefault(code[0], []).append((*paths, code[1:]) if code[0] == "R" else tuple(paths))
    return res


@pytest.mark.parametrize("target_advanced", [False, True], ids=["dich-dung-yen", "dich-da-tien-them"])
def test_a1_head_parent1_is_target_base_with_depth_2(tmp_path, target_advanced):
    o = build_origin(tmp_path, target_advanced)
    ci = shallow_checkout(tmp_path, o, depth=2)

    assert git(ci, "rev-parse", "HEAD") == o["merge"]
    assert git(ci, "rev-parse", "--is-shallow-repository") == "true"  # đúng là checkout nông
    assert git(ci, "rev-parse", "HEAD^1") == o["base"]  # cha thứ nhất = tip nhánh ĐÍCH
    assert git(ci, "rev-parse", "HEAD^2") == o["pr_head"]  # cha thứ hai = đầu PR
    if target_advanced:  # HEAD^1 là tip đích hiện tại, KHÔNG phải điểm rẽ nhánh
        assert o["base"] != o["fork_point"]
        assert git(ci, "rev-parse", "HEAD^1") != o["fork_point"]

    _ok(f"depth=2 ({'đích đã tiến thêm' if target_advanced else 'đích đứng yên'}): "
        "HEAD^1 = tip nhánh đích, HEAD^2 = đầu PR")


@pytest.mark.parametrize("target_advanced", [False, True], ids=["dich-dung-yen", "dich-da-tien-them"])
def test_a2_name_status_M_extracts_added_and_renamed_of_the_pr_only(tmp_path, target_advanced):
    o = build_origin(tmp_path, target_advanced)
    ci = shallow_checkout(tmp_path, o, depth=2)

    got = parse_name_status(git(ci, "diff", "--name-status", "-M", "HEAD^1", "HEAD"))

    assert got["A"] == [("app/routers/new_endpoint.py",)]
    assert got["R"] == [("app/old_name.py", "app/new_name.py", "100")]  # đổi tên KHÔNG bị đếm thành D + A
    assert got["M"] == [("app/keep.py",)]
    assert got["D"] == [("app/gone.py",)]
    # thay đổi riêng của nhánh đích không được lọt vào diff của PR (đây là lý do phải dùng HEAD^1, không phải fork-point)
    assert all("main_only" not in p for group in got.values() for entry in group for p in entry)
    assert sum(len(v) for v in got.values()) == 4

    # nội dung phía base đọc được qua `git show HEAD^1:path` (P2-2 cần để tính head − base)
    assert git(ci, "show", "HEAD^1:app/old_name.py").startswith("line 0:")
    _ok("`diff --name-status -M HEAD^1 HEAD`: đúng 1 A + 1 R100 (không thành D+A) + 1 M + 1 D; "
        "không lẫn thay đổi của nhánh đích; `git show HEAD^1:<path>` đọc được")


def test_a3_depth_1_has_no_base_so_worker_must_report_error(tmp_path):
    """Đây chính là lỗi mặc định của actions/checkout (fetch-depth: 1) mà P2-7 sửa. Git phải TỰ BÁO LỖI,
    không được im lặng trả diff rỗng — nếu không, dò nợ sẽ 'xanh giả'."""
    o = build_origin(tmp_path, target_advanced=True)
    ci = shallow_checkout(tmp_path, o, depth=1)

    assert git(ci, "rev-parse", "HEAD") == o["merge"]
    p = subprocess.run(["git", "diff", "--name-status", "-M", "HEAD^1", "HEAD"], cwd=ci, capture_output=True,
                       text=True, env={**os.environ, **_GIT_ENV})
    assert p.returncode != 0 and p.stdout == ""  # không có base -> lỗi rõ ràng, không phải "không có thay đổi"
    _ok("depth=1: HEAD^1 không phân giải được, git diff exit≠0 (không trả diff rỗng) -> worker trả `error`, đúng nguyên tắc bảo thủ §2")


# ───────────────────── B. Env QC_DIFF_BASE đi tới adapter và worker ─────────────────────

# Module chạy ở vai "tiến trình adapter" do core/runner._spawn dựng; nó lại dùng Adapter._exec thật để dựng
# tiến trình worker con — đúng hai chặng spawn của sản phẩm.
_PROBE = '''\
import json, os, sys
from pathlib import Path
from qc_agent.adapters._base import Adapter

WORKER_CODE = "import os, json; print(json.dumps({k: os.environ.get(k) for k in ('QC_DIFF_BASE', 'PROBE_EXTRA')}))"


class Probe(Adapter):
    NAME = "probe"
    ADAPTER_VERSION = "0.0.1"
    env = {"PROBE_EXTRA": "1"}  # adapter thật có thể khai env riêng: không được làm mất env thừa hưởng

    def build_cmd(self, spec, workdir):
        return [sys.executable, "-c", WORKER_CODE]

    def parse_output(self, proc, workdir, spec):
        raise NotImplementedError


sys.stdin.buffer.read()
proc = Probe()._exec(Probe().build_cmd({}, Path(".")), 30)
print(json.dumps({"adapter_process": os.environ.get("QC_DIFF_BASE"), "worker_process": json.loads(proc.stdout)}))
'''


@pytest.fixture()
def probe_module(tmp_path, monkeypatch):
    (tmp_path / "qc_diff_probe.py").write_text(_PROBE, encoding="utf-8")
    monkeypatch.setenv("PYTHONPATH", os.pathsep.join([str(tmp_path), os.environ.get("PYTHONPATH", "")]))
    return "qc_diff_probe"


def spawn_probe(module: str, tmp_path: Path) -> dict:
    rc, out, err = runner._spawn(module, {"budget": {"wallclock_s": 30}}, tmp_path / "runs")
    assert rc == 0, err
    return json.loads(out)


def test_b1_qc_diff_base_reaches_adapter_process_and_worker_process(tmp_path, monkeypatch, probe_module):
    monkeypatch.setenv("QC_DIFF_BASE", "HEAD^1")
    got = spawn_probe(probe_module, tmp_path)

    assert got["adapter_process"] == "HEAD^1"  # chặng 1: runner._spawn -> module adapter
    assert got["worker_process"] == {"QC_DIFF_BASE": "HEAD^1", "PROBE_EXTRA": "1"}  # chặng 2: Adapter._exec -> worker
    _ok("QC_DIFF_BASE=HEAD^1 tới được cả tiến trình adapter (runner._spawn) lẫn tiến trình worker (Adapter._exec); "
        "`Adapter.env` chồng thêm chứ không thay thế")


def test_b2_without_qc_diff_base_nothing_is_invented_so_full_scan(tmp_path, monkeypatch, probe_module):
    """D4: Mode 2 không đặt biến này => worker thấy None => full-scan. Không có tầng nào tự thêm giá trị mặc định."""
    monkeypatch.delenv("QC_DIFF_BASE", raising=False)
    got = spawn_probe(probe_module, tmp_path)

    assert got["adapter_process"] is None
    assert got["worker_process"]["QC_DIFF_BASE"] is None
    _ok("không đặt QC_DIFF_BASE => cả hai chặng đều thấy None (D4: full-scan); không tầng nào tự bịa giá trị")


def test_b3_executor_default_scrub_list_does_not_strip_qc_diff_base():
    """Executor (Mode 2) lọc env trước khi spawn CLI: chỉ được lọc biến DB, không đụng QC_DIFF_BASE."""
    from qc_agent.jobs.executor import ExecutorConfig  # noqa: PLC0415 — import trễ: chỉ test này cần module jobs

    assert "QC_DIFF_BASE" not in ExecutorConfig.scrub_env
    _ok("scrub_env mặc định của executor chỉ chứa biến DB, không chứa QC_DIFF_BASE")
