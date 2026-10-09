"""Dựng BẢN SAO CỤC BỘ của repo sandbox từ noteboard + PRD mẫu, chạy `qc-agent init` (tất định, offline), và IN danh sách việc phải làm trên GitHub.

Dry-run (mặc định): dựng vào thư mục tạm, báo kết quả rồi xoá: kiểm được toàn bộ phần cục bộ mà không để lại gì. `--apply-local DIR` ghi thật vào DIR (idempotent: chạy lại
không đè gì; DIR đã là sandbox thì chỉ báo "đã có"). Không bao giờ tạo repo, đẩy mã, tạo secret hay bật branch protection: các việc đó chỉ được IN (loại `external`).

Sandbox bắt đầu KHÔNG có PRD, không có `.qc-agent/ground-truth/` và không có suite `gt-functional`: `prepare A` thêm PRD, kịch bản A sinh GT, B duyệt GT. Trước khi A/B xong, gate của policy `noteboard`
(liệt kê gt-functional trong blocking_suites) chưa được kiểm chứng trên repo thiếu suite này; chỉ chạy C/D/E sau khi B đã merge GT.
"""
from __future__ import annotations

import shutil
import sys
import tempfile
from pathlib import Path

from tools.e2e.common import EXTERNAL, OFFLINE, ROOT, Step, git, run_local

FIXTURE = ROOT / "tests" / "fixtures" / "sut" / "noteboard"
OPENAPI = ROOT / "tests" / "fixtures" / "openapi" / "noteboard.json"
IGNORE = shutil.ignore_patterns("node_modules", "__pycache__", ".hypothesis", ".deepeval", "midscene_run", "runs", ".git", ".pytest_cache", "*.pyc")
DROP = (Path(".qc-agent") / "ground-truth", Path(".qc-agent") / "suites" / "gt-functional.yaml")


def _init_argv(dest: Path, intake: dict) -> list[str]:
    gh, sut = intake.get("github", {}), intake.get("sut", {})
    argv = [sys.executable, "-m", "qc_agent.core.cli", "init", "--sut-root", str(dest), "--slug", sut.get("project_slug") or "noteboard", "--openapi", str(dest / "docs" / "openapi.json"),
            "--prd-glob", sut.get("prd_glob") or "docs/prd/**", "--sut-dockerfile", sut.get("dockerfile") or "Dockerfile", "--sut-context", sut.get("context") or ".",
            "--sut-port", str(sut.get("port") or 8000), "--health-path", sut.get("health_path") or "/", "--qc-repo", gh.get("qc_agent_repo") or "Muteen-Felix/QC-Agent"]
    for flag, value in (("--qa-team", gh.get("qa_team")), ("--qc-ref", gh.get("qc_ref")), ("--image", gh.get("image"))):
        if value:
            argv += [flag, str(value)]
    return argv


def build_local(dest: Path, intake: dict) -> dict:
    """Dựng sandbox vào `dest`. Trả báo cáo {status, files, init_exit, init_output, notes}. Không chạm mạng."""
    dest = Path(dest)
    if (dest / ".git").exists() and (dest / "toyapp" / "app.py").is_file():
        return {"status": "đã có", "files": _count(dest), "init_exit": None, "init_output": "", "notes": ["DIR đã là sandbox: không đụng gì"]}
    if dest.exists() and any(dest.iterdir()):
        raise FileExistsError(f"{dest} không rỗng và không phải sandbox: từ chối ghi vào")
    shutil.copytree(FIXTURE, dest, ignore=IGNORE, dirs_exist_ok=True)
    for rel in DROP:
        target = dest / rel
        shutil.rmtree(target, ignore_errors=True) if target.is_dir() else target.unlink(missing_ok=True)
    (dest / "docs").mkdir(parents=True, exist_ok=True)
    shutil.copyfile(OPENAPI, dest / "docs" / "openapi.json")   # PRD KHÔNG vào commit base: `prepare A` thêm nó (xem scenarios.add_prd)
    done = run_local(_init_argv(dest, intake), cwd=ROOT, timeout=120)
    notes = []
    if done.returncode != 0:
        raise RuntimeError(f"qc-agent init exit {done.returncode}: {(done.stdout + done.stderr)[-600:]}")
    if "TODO" in done.stdout or "qc-agent:todo" in done.stdout:
        notes.append("init để lại dấu qc-agent:todo (thiếu --qa-team/--qc-ref/--image trong intake): điền rồi chạy lại trước khi đẩy lên GitHub")
    git(dest, "init", "-q")
    git(dest, "symbolic-ref", "HEAD", "refs/heads/main")
    git(dest, "add", "-A")
    git(dest, "commit", "-qm", "chore(sandbox): base noteboard + PRD mẫu")
    return {"status": "đã dựng", "files": _count(dest), "init_exit": done.returncode, "init_output": done.stdout, "notes": notes}


def _count(root: Path) -> int:
    return sum(1 for p in root.rglob("*") if p.is_file() and ".git" not in p.parts)


def dry_run(intake: dict) -> dict:
    """Dựng thử vào thư mục tạm rồi xoá. Trả cùng báo cáo, kèm danh sách file quan trọng đã sinh."""
    with tempfile.TemporaryDirectory(prefix="qc-e2e-sandbox-") as temp:
        dest = Path(temp) / "sandbox"
        report = build_local(dest, intake)
        report["generated"] = sorted(str(p.relative_to(dest)).replace("\\", "/") for p in (dest / ".github").rglob("*") if p.is_file()) + \
            [f".qc-agent/suites/{p.name}" for p in sorted((dest / ".qc-agent" / "suites").glob("*.yaml"))]
        report["has_ground_truth"] = (dest / ".qc-agent" / "ground-truth").exists()
        try:
            shutil.rmtree(dest, onerror=lambda func, target, _e: (Path(target).chmod(0o700), func(target)))   # .git có file chỉ-đọc trên Windows
        except OSError:
            pass
        return report


def secrets(intake: dict) -> list[tuple[str, str, str]]:
    """(tên secret, dùng cho, bắt buộc?) — chỉ TÊN, không bao giờ giá trị."""
    out = [("ANTHROPIC_API_KEY", "Select (C, D, 10 lượt) và sinh GT (A); đặt spend limit ở workspace", "bắt buộc cho mọi lượt LLM"),
           ("JIRA_BASE_URL", "tạo ticket Low (D)", "bắt buộc cho D"), ("JIRA_EMAIL", "tạo ticket Low (D)", "bắt buộc cho D"), ("JIRA_API_TOKEN", "tạo ticket Low (D)", "bắt buộc cho D"),
           ("qc_bot_token", "để PR sinh GT kích hoạt check validate (GITHUB_TOKEN thì không)", "tuỳ chọn, nên có")]
    if (intake.get("sut", {}).get("db") or {}).get("needed"):
        out += [("SUT_SECRET_ENV", "KEY=VALUE nhiều dòng cho container SUT (DATABASE_URL...)", "bắt buộc (SUT cần DB)"), ("SUT_DB_SECRET_ENV", "KEY=VALUE chỉ cho container db", "bắt buộc (SUT cần DB)")]
    if (intake.get("llm") or {}).get("judge_and_midscene_keys"):
        out += [("OPENAI_API_KEY", "G-Eval judge: KÊNH LLM THỨ HAI, ngoài ước tính Anthropic", "tuỳ chọn"), ("GEMINI_API_KEY", "judge dự phòng / sinh GT bằng Gemini", "tuỳ chọn"),
                ("MIDSCENE_MODEL_API_KEY", "UI explore (Midscene) + MIDSCENE_MODEL_BASE_URL/NAME/FAMILY", "tuỳ chọn")]
    return out


def plan(intake: dict, dest: str = "<sandbox-dir>") -> list[Step]:
    gh = intake.get("github", {})
    repo = gh.get("sandbox_repo") or "<OWNER/REPO>"
    visibility = "--public" if str(gh.get("visibility")).lower() != "private" else "--private"
    steps = [Step(OFFLINE, "Dựng bản sao sandbox cục bộ (dry-run: thư mục tạm rồi xoá)", command="python -m tools.e2e sandbox --intake runs/e2e/intake.yaml"),
             Step(OFFLINE, "Dựng thật vào thư mục", command=f"python -m tools.e2e sandbox --intake runs/e2e/intake.yaml --apply-local {dest}"),
             Step(EXTERNAL, "Tạo repo sandbox trên GitHub và đẩy base", needs=("bạn xác nhận tên repo, gói và chế độ public/private",),
                  command=f"gh repo create {repo} {visibility} --source {dest} --remote origin --push"),
             Step(EXTERNAL, "Bật cho Actions mở PR (kịch bản A cần)", needs=("quyền admin repo",),
                  command=f"gh api -X PUT repos/{repo}/actions/permissions/workflow -f default_workflow_permissions=read -F can_approve_pull_request_reviews=true")]
    for name, why, need in secrets(intake):
        steps.append(Step(EXTERNAL, f"Tạo secret {name} ({why})", needs=(need, "bạn tự nhập giá trị; không đưa vào dòng lệnh"), command=f"gh secret set {name} --repo {repo}"))
    steps.append(Step(EXTERNAL, "Đưa user_map vào policy của qc-agent@main (ticket D cần người nhận)", needs=("PR vào qc-agent được duyệt và merge",),
                      note="policy lấy từ qc-agent@main lúc chạy; configs/projects/noteboard.yaml hiện có `user_map: {}` và project_key QCSB. Đây là sửa repo qc-agent, không phải sandbox."))
    return steps
