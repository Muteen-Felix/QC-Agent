"""Chạy các bước SHELL của .github/workflows/qc-gate.reusable.yml (hoặc job khác của workflow tái sử dụng: `--workflow ... --job validate`) trên Docker cục bộ, để kiểm thử workflow mà không cần GitHub.

Không mô phỏng toàn bộ GitHub Actions: chỉ các bước `run:` (bỏ `uses:` như checkout/login/upload-artifact), suy giá trị `${{ }}` từ
inputs/secrets/github/steps giả, ghi/đọc $GITHUB_OUTPUT, và bắt chước quy tắc `if: always()` (sau khi một bước lỗi, chỉ chạy các bước always()).

    python tools/run_reusable_locally.py --workspace tests/fixtures/sut/noteboard --input project=noteboard \\
        --input image=qc-agent:dev --input allow_unpinned_image=true --secret QC_API_TOKEN=... --github-api http://host.docker.internal:9999

`--policy-dir DIR` thay bước "Fetch policy": DIR được chép vào $RUNNER_TEMP/qc-policy y như bản fetch từ qc-agent@main. Không đặt thì bước đó chạy
THẬT, gọi $GITHUB_API_URL (đổi bằng --github-api để trỏ vào server giả).

`--base-sha SHA` (cùng `--sha` làm head) điền `github.event.pull_request.base.sha`, để bước "Select (PR)" có đủ base/head thật (workspace phải là repo git
chứa cả hai commit). Không đặt thì event không có `base.sha` và Select lùi về FULL SET, đúng như trước.

`--anthropic-api URL` / `--jira-api URL` trỏ bước Select (PR) và bước Jira (Low) vào server giả (đặt kèm `--secret ANTHROPIC_API_KEY=...`; thiếu thì dùng khoá giả).
`--event-name workflow_dispatch` chạy workflow như chạy tay (kèm `--input workers=semgrep`). `--scenario full-chain` chạy kịch bản A–E trên noteboard (xem tests/full_chain.py).
Image đưa vào `--input image=...` được kiểm bằng tools/image_check.py (phải build từ HEAD sạch; QC_HARNESS_ALLOW_STALE_IMAGE=1 để bỏ qua, kết quả mang nhãn "IMAGE CHƯA XÁC MINH").

ĐIỂM LỆCH DUY NHẤT của harness so với workflow thật (`step_rewrites`): bước "Select (PR)" của workflow chỉ chuyển `-e ANTHROPIC_API_KEY` vào container, KHÔNG chuyển
`ANTHROPIC_BASE_URL`, nên fake LLM không nhận được lời gọi. Harness đổi đúng một chuỗi trong lệnh `docker run` của bước đó để thêm `-e ANTHROPIC_BASE_URL`; chuỗi cũ phải
khớp ĐÚNG MỘT LẦN, không thì harness dừng (workflow đổi thì harness không âm thầm chạy sai). Mọi thứ khác chạy nguyên văn.

Cần bash (Git Bash trên Windows) và docker. Exit code = kết quả bước cuối (Enforce gate result).
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_WORKFLOW = ROOT / ".github" / "workflows" / "qc-gate.reusable.yml"
_EXPR = re.compile(r"\$\{\{\s*(.+?)\s*\}\}")


def find_bash() -> str:
    if os.name == "nt":
        candidates = [r"C:\Program Files\Git\bin\bash.exe", r"C:\Program Files (x86)\Git\bin\bash.exe"]
        git = shutil.which("git")
        if git:   # Git cài theo người dùng (%LOCALAPPDATA%\Programs\Git): bash nằm cạnh; bash.exe trong WindowsApps là WSL, không dùng được
            candidates.append(str(Path(git).resolve().parent.parent / "bin" / "bash.exe"))
        for candidate in candidates:
            if Path(candidate).is_file():
                return candidate
    found = shutil.which("bash")
    if not found:
        raise SystemExit("không tìm thấy bash")
    return found


class Context:
    def __init__(self, inputs: dict, secrets: dict, github: dict):
        self.inputs, self.secrets, self.github, self.steps = inputs, secrets, github, {}

    def lookup(self, path: str):
        parts = path.split(".")
        root = {"inputs": self.inputs, "secrets": self.secrets, "github": self.github, "steps": self.steps}.get(parts[0])
        for part in parts[1:]:
            root = root.get(part) if isinstance(root, dict) else None
        return root

    def _atom(self, atom: str):
        """Một vế: `A == 'x'` / `A != 'x'` (ra bool), chuỗi 'literal', hoặc đường dẫn chấm."""
        comparison = re.fullmatch(r"(\S+)\s*(==|!=)\s*'([^']*)'", atom)
        if comparison:
            value = self.lookup(comparison.group(1))
            equal = ("" if value is None else str(value).lower() if isinstance(value, bool) else str(value)) == comparison.group(3)
            return equal == (comparison.group(2) == "==")
        if atom.startswith("'") and atom.endswith("'"):
            return atom[1:-1]
        return self.lookup(atom)

    def evaluate(self, expression: str) -> str:
        """Hỗ trợ: đường dẫn chấm, chuỗi 'literal', so sánh `==`/`!=` với literal, `&&` và `||` theo ngữ nghĩa của GitHub
        (`a && b` cho b nếu a đúng, không thì a; `a || b` cho vế đầu tiên khác rỗng) => `github.event_name == 'x' && 'v' || ''` chạy đúng."""
        for operand in (o.strip() for o in expression.split("||")):
            value = None
            for atom in (a.strip() for a in operand.split("&&")):
                value = self._atom(atom)
                if value in (None, "", False):
                    break  # vế đầu của && sai: cả toán hạng nhận giá trị sai đó
            if value not in (None, "", False):
                return str(value).lower() if isinstance(value, bool) else str(value)
        return ""

    def check(self, condition: str) -> bool:
        """Điều kiện `if:` của bước: chỉ hỗ trợ các vế `always()`, `A == 'x'`, `A != 'x'` nối bằng `&&` (thứ khác => báo lỗi để không chạy sai âm thầm)."""
        for part in (p.strip() for p in condition.split("&&")):
            if part == "always()":
                continue
            match = re.fullmatch(r"(\S+)\s*(==|!=)\s*'([^']*)'", part)
            if not match:
                raise SystemExit(f"harness chưa hỗ trợ điều kiện if: {part!r}")
            value = self.lookup(match.group(1))
            equal = ("" if value is None else str(value).lower() if isinstance(value, bool) else str(value)) == match.group(3)
            if equal != (match.group(2) == "=="):
                return False
        return True

    def render(self, text) -> str:
        return _EXPR.sub(lambda m: self.evaluate(m.group(1)), str(text))


LOCAL_POLICY_REF = "0123456789abcdef0123456789abcdef01234567"   # commit giả cho `--policy-dir`
FETCH_STEP = "Fetch policy"
SELECT_STEP = "Select (PR)"
# Điểm lệch có tên (xem docstring đầu file). Test tĩnh (tests/test_harness_rewrites.py) khẳng định workflow còn đúng một chuỗi này.
SELECT_BASE_URL_REWRITE = ("-e ANTHROPIC_API_KEY -e QC_SELECT_CACHE_DIR=/cache", "-e ANTHROPIC_API_KEY -e ANTHROPIC_BASE_URL -e QC_SELECT_CACHE_DIR=/cache")
FAKE_ANTHROPIC_KEY = "sk-ant-harness-fake"


def anthropic_rewrites() -> dict:
    """`step_rewrites` để fake LLM nhận được lời gọi của bước Select (kèm `step_env[SELECT_STEP]["ANTHROPIC_BASE_URL"]`)."""
    return {SELECT_STEP: [SELECT_BASE_URL_REWRITE]}


def apply_rewrites(steps: list, step_rewrites: dict) -> dict[str, str]:
    """Trả {tên bước: nội dung `run:` sau khi đổi}. Bước không có trong workflow, hoặc chuỗi cũ khớp khác đúng một lần => dừng (không chạy sai âm thầm)."""
    by_name = {step.get("name"): step for step in steps if "run" in step}
    out = {}
    for name, edits in step_rewrites.items():
        if name not in by_name:
            raise SystemExit(f"step_rewrites: workflow không có bước {name!r} (workflow đã đổi tên bước?)")
        text = by_name[name]["run"]
        for old, new in edits:
            count = text.count(old)
            if count != 1:
                raise SystemExit(f"step_rewrites: chuỗi cần đổi khớp {count} lần (cần đúng 1) trong bước {name!r}: {old!r}; workflow đã đổi, cập nhật harness")
            text = text.replace(old, new)
        out[name] = text
    return out


def _posix(path) -> str:
    """Đường dẫn cho bash + `docker -v` (Git Bash trên Windows: C:/x -> /c/x)."""
    text = str(path)
    if os.name == "nt" and len(text) > 1 and text[1] == ":":
        return "/" + text[0].lower() + text[2:].replace(chr(92), "/")
    return text


def _native_posix(path) -> str:
    """$RUNNER_TEMP: nơi workflow ghi file mà chính docker CLI đọc (`--env-file`), nên phải là đường dẫn Windows hợp lệ (`C:/x`, dấu gạch chéo) chứ không phải
    `/c/x` của Git Bash. Cả bash lẫn `docker -v` đều nhận dạng này. Linux/macOS: giữ nguyên."""
    return Path(path).as_posix() if os.name == "nt" else str(path)


def run_workflow(workflow_path, workspace, inputs: dict, secrets: dict, github: dict, *, skip=("Pull qc-agent image",), echo=print,
                 policy_dir=None, step_env=None, runner_temp=None, job: str = "gate", step_rewrites=None) -> dict:
    data = yaml.safe_load(Path(workflow_path).read_text(encoding="utf-8"))
    declared = data[True if True in data else "on"]["workflow_call"]["inputs"]
    merged = {name: spec.get("default") for name, spec in declared.items()}
    merged.update(inputs)
    missing = [name for name, spec in declared.items() if spec.get("required") and merged.get(name) in (None, "")]
    if missing:
        raise SystemExit(f"thiếu input bắt buộc: {', '.join(missing)}")
    ctx = Context(merged, secrets, github)
    workflow_env = {k: str(v) for k, v in (data.get("env") or {}).items()}
    steps = data["jobs"][job]["steps"]
    rewritten = apply_rewrites(steps, step_rewrites or {})   # kiểm TRƯỚC khi chạy bước nào
    bash, failed, results = find_bash(), False, {}
    with tempfile.TemporaryDirectory() as tmp:
        output_file = Path(tmp) / "github_output"
        runner_temp = Path(runner_temp) if runner_temp else Path(tmp) / "runner_temp"   # đặt --runner-temp để giữ lại refine.patch sau khi chạy
        runner_temp.mkdir(parents=True, exist_ok=True)
        if policy_dir is not None:   # thay bước fetch: cùng vị trí như bản fetch thật
            shutil.rmtree(runner_temp / "qc-policy", ignore_errors=True)
            shutil.copytree(policy_dir, runner_temp / "qc-policy")
            ctx.steps["policy"] = {"outputs": {"ref": LOCAL_POLICY_REF}, "outcome": "success"}
        for step in steps:
            name = step.get("name") or step.get("uses", "?")
            if "run" not in step:
                echo(f"[skip] {name} (uses:)")
                continue
            if name in skip or (policy_dir is not None and name == FETCH_STEP):
                echo(f"[skip] {name}")
                continue
            condition = str(step.get("if", ""))
            if condition and not ctx.check(condition):
                echo(f"[skip] {name} (điều kiện if sai)")
                continue
            if failed and "always()" not in condition:
                echo(f"[skip] {name} (bước trước lỗi)")
                continue
            output_file.write_text("", encoding="utf-8")
            env = {**os.environ, "MSYS_NO_PATHCONV": "1", "GITHUB_OUTPUT": _posix(output_file), "RUNNER_TEMP": _native_posix(runner_temp), **workflow_env}
            env.update({k: str(v) for k, v in github.get("env", {}).items()})
            env.update({k: ctx.render(v) for k, v in (step.get("env") or {}).items()})
            for key, value in (step_env or {}).get(name, {}).items():   # ghi đè theo tên bước; giá trị None = xoá biến
                if value is None:
                    env.pop(key, None)
                else:
                    env[key] = value
            echo(f"[run ] {name}")
            proc = subprocess.run([bash, "-e", "-c", rewritten.get(name, step["run"])], cwd=workspace, env=env, text=True, encoding="utf-8",
                                  capture_output=True)
            tail = int(os.environ.get("QC_HARNESS_TAIL", "25"))   # số dòng cuối của MỖI luồng (stdout, stderr) được in
            # Như runner của GitHub: dòng `::add-mask::<giá trị>` là lệnh, không phải log. Nuốt nó (không in) và ghi lại để test biết giá trị nào đã được yêu cầu che;
            # KHÔNG thay giá trị bằng `***` trong log, nên giá trị mà script lỡ in ra ở chỗ khác vẫn lộ trong log harness và test bắt được.
            prefix = "::add-mask::"
            masks = [line[len(prefix):] for line in proc.stdout.splitlines() if line.startswith(prefix)]
            shown = [line for line in proc.stdout.splitlines() if not line.startswith(prefix)]
            for line in shown[-tail:] + proc.stderr.splitlines()[-tail:]:
                echo("       " + line)
            outputs = {}
            for line in output_file.read_text(encoding="utf-8").splitlines():
                if "=" in line:
                    key, _, value = line.partition("=")
                    outputs[key] = value
            if step.get("id"):
                ctx.steps[step["id"]] = {"outputs": outputs, "outcome": "success" if proc.returncode == 0 else "failure"}
            results[name] = {"returncode": proc.returncode, "outputs": outputs, "masks": masks}
            if proc.returncode != 0 and not step.get("continue-on-error"):
                failed = True
    return results


def build_github_context(tmp, *, api_url: str, sha: str = "abc1234def5678", base_sha: str | None = None, pr: int = 7, token: str = "ghs_local_test_token",
                         repository: str = "o/r", event_name: str = "pull_request", run_id: str = "1001", run_attempt: str = "1", dispatch_inputs=None) -> dict:
    """Dict `github` cho run_workflow + file event.json trong `tmp`. `workflow_dispatch` không có `pull_request` (các bước `if: ... == 'pull_request'` bị bỏ qua)."""
    event = Path(tmp) / "event.json"
    if event_name == "pull_request":
        pull_request = {"number": pr, "head": {"sha": sha, "ref": "feat/x"}}
        if base_sha:
            pull_request["base"] = {"sha": base_sha}
        payload = {"pull_request": pull_request}
        parsed = {"pull_request": {**pull_request, "head": {"sha": sha}}}
    elif event_name == "workflow_dispatch":
        payload = parsed = {"inputs": dict(dispatch_inputs or {}), "ref": "refs/heads/main"}
    else:
        raise SystemExit(f"event_name chưa hỗ trợ: {event_name!r} (pull_request | workflow_dispatch)")
    event.write_text(json.dumps(payload), encoding="utf-8")
    return {"token": token, "sha": "mergecommit0000", "actor": "tester", "run_attempt": run_attempt, "event_name": event_name, "event": parsed,
            "env": {"GITHUB_EVENT_PATH": str(event), "GITHUB_REPOSITORY": repository, "GITHUB_SHA": "mergecommit0000", "GITHUB_RUN_ID": run_id,
                    "GITHUB_RUN_ATTEMPT": run_attempt, "GITHUB_SERVER_URL": "https://github.com", "GITHUB_API_URL": api_url, "GITHUB_REF_NAME": "feat/x"}}


def main(argv=None) -> int:
    for stream in (sys.stdout, sys.stderr):   # Windows: console cp1252 không in được tiếng Việt
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(errors="replace")
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--workflow", default=str(DEFAULT_WORKFLOW))
    ap.add_argument("--job", default="gate", help="job của workflow cần chạy (mặc định gate; qc-groundtruth.reusable.yml có validate)")
    ap.add_argument("--workspace", help="thư mục repo SUT (có Dockerfile và .qc-agent/suites); bắt buộc trừ khi có --scenario")
    ap.add_argument("--input", action="append", default=[], metavar="K=V")
    ap.add_argument("--secret", action="append", default=[], metavar="K=V")
    ap.add_argument("--github-api", default="https://api.github.com")
    ap.add_argument("--repository", default="o/r")
    ap.add_argument("--sha", default="abc1234def5678")
    ap.add_argument("--base-sha", default=None, help="base.sha của PR; thiếu thì Select (PR) lùi về FULL SET")
    ap.add_argument("--pr", type=int, default=7)
    ap.add_argument("--token", default="ghs_local_test_token")
    ap.add_argument("--runner-temp", metavar="DIR", help="dùng thư mục này làm $RUNNER_TEMP (giữ lại policy và refine/ sau khi chạy)")
    ap.add_argument("--policy-dir", metavar="DIR", help="dùng thư mục này làm policy thay vì fetch từ qc-agent@main (không cần mạng)")
    ap.add_argument("--anthropic-api", metavar="URL", help="fake Messages API cho bước Select (PR); server phải bind 0.0.0.0 và URL nhìn được từ container (host.docker.internal)")
    ap.add_argument("--jira-api", metavar="URL", help="JIRA_BASE_URL cho bước Jira (Low). Lưu ý: jira.py chỉ nhận https hoặc http tới loopback, nên URL host.docker.internal bị bỏ qua")
    ap.add_argument("--event-name", default="pull_request", choices=["pull_request", "workflow_dispatch"])
    ap.add_argument("--scenario", choices=["full-chain"], help="chạy kịch bản A–E trên noteboard (tests/full_chain.py) và in bảng kết quả; bỏ qua các tham số workspace/input")
    args = ap.parse_args(argv)
    if args.scenario:
        sys.path.insert(0, str(ROOT))
        from tests import full_chain   # noqa: PLC0415 — chỉ cần khi chạy kịch bản
        return full_chain.main()
    if not args.workspace:
        ap.error("thiếu --workspace (chỉ có thể bỏ khi dùng --scenario)")
    kv = lambda items: dict(item.split("=", 1) for item in items)  # noqa: E731
    inputs, secrets = kv(args.input), kv(args.secret)
    if inputs.get("image"):
        import image_check   # noqa: PLC0415 — nằm cạnh file này trong tools/
        try:
            verdict = image_check.verify(inputs["image"])
        except image_check.ImageCheckError as error:
            print(f"image: {error}", file=sys.stderr)
            return 1
        print(verdict.label)
    step_env, step_rewrites = {}, None
    if args.anthropic_api:
        step_env[SELECT_STEP] = {"ANTHROPIC_BASE_URL": args.anthropic_api}
        step_rewrites = anthropic_rewrites()
        secrets.setdefault("ANTHROPIC_API_KEY", FAKE_ANTHROPIC_KEY)
    if args.jira_api:
        secrets["JIRA_BASE_URL"] = args.jira_api
        secrets.setdefault("JIRA_EMAIL", "qc@example.invalid")
        secrets.setdefault("JIRA_API_TOKEN", "harness-fake")
    with tempfile.TemporaryDirectory() as tmp:
        github = build_github_context(tmp, api_url=args.github_api, sha=args.sha, base_sha=args.base_sha, pr=args.pr, token=args.token, repository=args.repository,
                                      event_name=args.event_name, dispatch_inputs={k: inputs[k] for k in ("workers", "suites") if k in inputs})
        results = run_workflow(args.workflow, args.workspace, inputs, secrets, github, policy_dir=args.policy_dir, runner_temp=args.runner_temp, job=args.job,
                               step_env=step_env, step_rewrites=step_rewrites)
    if args.job != "gate":   # job không có bước "Enforce gate result": kết quả là bước cuối cùng đã chạy
        return next(reversed(results.values()), {}).get("returncode", 1)
    return results.get("Enforce gate result", {}).get("returncode", 1)


if __name__ == "__main__":
    sys.exit(main())
