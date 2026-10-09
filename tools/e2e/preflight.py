"""Preflight S4-06: kiểm mọi thứ kiểm được CỤC BỘ và nêu rõ phần cần thông tin/quyền bên ngoài. Không gọi mạng; không sửa SUT.

Với SUT có `local_path`, lệnh test chỉ chạy trên BẢN SAO TẠM (`--run-sut-tests`), theo argv (không shell), có timeout. Lệnh build (docker) chỉ được in.
"""
from __future__ import annotations

import shlex
import shutil
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path

import yaml

from tools.e2e import intake as intake_mod
from tools.e2e.common import ROOT, git, run_local

OK, FAIL, EXT, SKIP = "LOCAL_OK", "LOCAL_FAIL", "CẦN_NGOÀI", "BỎ_QUA"
COPY_IGNORE = shutil.ignore_patterns("node_modules", "__pycache__", ".hypothesis", ".deepeval", "midscene_run", "runs", ".git", ".pytest_cache", "*.pyc")


@dataclass
class Check:
    name: str
    status: str
    detail: str


def _repo_state() -> list[Check]:
    out = []
    branch, head = git(ROOT, "rev-parse", "--abbrev-ref", "HEAD"), git(ROOT, "rev-parse", "--short=10", "HEAD")
    dirty = [line for line in git(ROOT, "status", "--porcelain").splitlines() if line.strip()]
    out.append(Check("repo qc-agent", OK, f"nhánh {branch} · HEAD {head} · {len(dirty)} thay đổi chưa commit ({', '.join(line.strip() for line in dirty[:5]) or 'sạch'})"))
    freeze = run_local([sys.executable, "tools/freeze_contract.py", "--check"], cwd=ROOT, timeout=120)
    out.append(Check("contract còn nguyên", OK if freeze.returncode == 0 else FAIL, (freeze.stdout.strip() or freeze.stderr.strip())[-160:]))
    return out


def _policy(slug: str) -> list[Check]:
    path = ROOT / "configs" / "projects" / f"{slug}.yaml"
    if not path.is_file():
        return [Check("policy của project", EXT, f"configs/projects/{slug}.yaml chưa có trong repo này: gate sẽ dùng _default.yaml (không có gt-functional, không có Jira). "
                                                  "Policy lấy từ qc-agent@main khi chạy, nên đăng ký project là một PR vào qc-agent")]
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    jira = data.get("jira") or {}
    blocking = ((data.get("modes") or {}).get("pr") or {}).get("blocking_suites")
    out = [Check("policy của project", OK, f"{slug}.yaml có; blocking_suites(pr)={blocking}; jira.project_key={jira.get('project_key')!r}; user_map có {len(jira.get('user_map') or {})} mục")]
    if not jira.get("user_map"):
        out.append(Check("Jira user_map trong policy", EXT, "rỗng: ticket D sẽ không có người nhận; cần PR vào qc-agent@main điền {github_login: accountId}"))
    return out


def _sut(data: dict, run_tests: bool) -> list[Check]:
    sut = data.get("sut") or {}
    local = Path(sut["local_path"]) if sut.get("local_path") else None
    if local is None:
        return [Check("SUT cục bộ", EXT, "intake.sut.local_path trống: không kiểm được Dockerfile/PRD/OpenAPI/suite; cần đường dẫn bản checkout của SUT")]
    root = local if local.is_absolute() else ROOT / local
    if not root.is_dir():
        return [Check("SUT cục bộ", FAIL, f"{root} không tồn tại")]
    out = [Check("SUT cục bộ", OK, f"{root}")]
    dockerfile = root / (sut.get("dockerfile") or "Dockerfile")
    out.append(Check("Dockerfile", OK if dockerfile.is_file() else FAIL, str(dockerfile.relative_to(root)) if dockerfile.is_file() else f"không thấy {sut.get('dockerfile') or 'Dockerfile'}"))
    suites = list((root / ".qc-agent" / "suites").glob("*.yaml")) if (root / ".qc-agent" / "suites").is_dir() else []
    out.append(Check("suite đã duyệt (.qc-agent/suites)", OK if suites else EXT, f"{len(suites)} file" if suites else "chưa có: `qc-agent init` rồi QA duyệt; sandbox bắt đầu từ trạng thái này"))
    openapi = root / (sut.get("openapi") or "docs/openapi.json")
    out.append(Check("OpenAPI", OK if openapi.is_file() else EXT, f"{openapi.name} có" if openapi.is_file() else "thiếu trong bản checkout: cần file OpenAPI đã commit hoặc URL của SUT đang chạy (sandbox noteboard tự chép bản mẫu)"))
    prd = list(root.glob(sut.get("prd_glob") or "docs/prd/**"))
    out.append(Check("PRD", OK if any(p.is_file() for p in prd) else EXT, f"{sum(p.is_file() for p in prd)} file" if prd else "chưa có PRD trong bản checkout (kịch bản A cần; sandbox noteboard tự chép PRD mẫu)"))
    owners = root / ".github" / "CODEOWNERS"
    out.append(Check("CODEOWNERS bảo vệ .qc-agent/", OK if owners.is_file() and "/.qc-agent/" in owners.read_text(encoding="utf-8") else EXT, "có dòng /.qc-agent/" if owners.is_file() else "chưa có (qc-agent init sinh)"))
    out.append(Check("lệnh build SUT", SKIP, f"không chạy (cần Docker, kéo base image qua mạng): {sut.get('build_command') or 'chưa khai'}"))
    cmd = sut.get("test_command")
    if not cmd:
        out.append(Check("lệnh test SUT", EXT, "intake.sut.test_command trống"))
    elif not run_tests:
        out.append(Check("lệnh test SUT", SKIP, f"chưa chạy (thêm --run-sut-tests): {cmd}"))
    else:
        out.append(_run_tests(root, cmd))
    return out


def _run_tests(root: Path, cmd: str) -> Check:
    with tempfile.TemporaryDirectory(prefix="qc-e2e-sut-copy-") as temp:
        copy = Path(temp) / "sut"
        shutil.copytree(root, copy, ignore=COPY_IGNORE)
        argv = shlex.split(cmd)
        if not argv:
            return Check("lệnh test SUT", FAIL, "test_command rỗng")
        if argv[0] in ("python", "python3"):
            argv[0] = sys.executable
        try:
            done = run_local(argv, cwd=copy, timeout=600)
        except Exception as error:   # lệnh ngoài danh sách trắng (vd. npm, pytest) hoặc timeout: nói thẳng, không đoán
            return Check("lệnh test SUT (bản sao tạm)", SKIP, f"không chạy được bằng công cụ này ({type(error).__name__}: {str(error)[:120]}); chạy tay trên bản sao: {cmd}")
        shutil.rmtree(copy, ignore_errors=True)
        return Check("lệnh test SUT (bản sao tạm)", OK if done.returncode == 0 else FAIL, f"exit {done.returncode}: {(done.stdout + done.stderr).strip()[-200:]}")


def run(data: dict, *, run_sut_tests: bool = False) -> list[Check]:
    checks = _repo_state()
    checks.append(Check("Docker CLI", OK if shutil.which("docker") else FAIL, "có trong PATH (không kiểm daemon)" if shutil.which("docker") else "không thấy `docker`: harness và build SUT cần Docker"))
    checks.append(Check("gh CLI", OK if shutil.which("gh") else EXT, "có trong PATH (công cụ này KHÔNG chạy nó)" if shutil.which("gh") else "không thấy `gh`: người chạy cần nó để mở PR và thu bằng chứng"))
    checks += _policy((data.get("sut") or {}).get("project_slug") or "noteboard")
    checks += _sut(data, run_sut_tests)
    for gap in intake_mod.validate(data):
        checks.append(Check(f"intake: {gap.key}", EXT, f"{'CHẶN' if gap.blocker else 'lưu ý'} [{gap.blocks}] {gap.reason}"))
    return checks


def render(checks: list[Check], *, title: str = "Preflight S4-06") -> str:
    local = [c for c in checks if c.status in (OK, FAIL, SKIP)]
    outside = [c for c in checks if c.status == EXT]
    lines = [f"# {title}", "", "Chạy OFFLINE: không gọi LLM/count_tokens/GitHub/Jira; không sửa SUT. Trạng thái S4-06: **CHƯA CÓ BẰNG CHỨNG GITHUB THẬT, KHÔNG tick DoD.**", "",
             "## Kiểm được cục bộ", "", "| Mục | Kết quả | Chi tiết |", "|---|---|---|"]
    lines += [f"| {c.name} | {c.status} | {c.detail} |" for c in local]
    lines += ["", "## Cần thông tin/quyền bên ngoài", "", "| Mục | Chi tiết |", "|---|---|"]
    lines += [f"| {c.name} | {c.detail} |" for c in outside] or ["| — | không còn mục nào |"]
    failed = [c for c in local if c.status == FAIL]
    lines += ["", f"Tổng: {len(local)} mục cục bộ ({len(failed)} FAIL), {len(outside)} mục cần bên ngoài."]
    return "\n".join(lines) + "\n"
