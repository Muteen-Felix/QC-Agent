"""Chạy các bước SHELL của .github/workflows/qc-gate.reusable.yml trên Docker cục bộ, để kiểm thử workflow mà không cần GitHub.

Không mô phỏng toàn bộ GitHub Actions: chỉ các bước `run:` (bỏ `uses:` như checkout/login/upload-artifact), suy giá trị `${{ }}` từ
inputs/secrets/github/steps giả, ghi/đọc $GITHUB_OUTPUT, và bắt chước quy tắc `if: always()` (sau khi một bước lỗi, chỉ chạy các bước always()).

    python tools/run_reusable_locally.py --workspace tests/fixtures/sut/noteboard --input project=noteboard \\
        --input image=qc-agent:dev --input allow_unpinned_image=true --secret QC_API_TOKEN=... --github-api http://host.docker.internal:9999

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
        for candidate in (r"C:\Program Files\Git\bin\bash.exe", r"C:\Program Files (x86)\Git\bin\bash.exe"):
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

    def evaluate(self, expression: str) -> str:
        """Chỉ hỗ trợ: đường dẫn chấm, chuỗi 'literal', và toán tử || (lấy giá trị đầu tiên khác rỗng)."""
        for operand in (o.strip() for o in expression.split("||")):
            if operand.startswith("'") and operand.endswith("'"):
                value = operand[1:-1]
            else:
                value = self.lookup(operand)
            if value not in (None, "", False):
                return str(value).lower() if isinstance(value, bool) else str(value)
        return ""

    def render(self, text) -> str:
        return _EXPR.sub(lambda m: self.evaluate(m.group(1)), str(text))


def run_workflow(workflow_path, workspace, inputs: dict, secrets: dict, github: dict, *, skip=("Pull qc-agent image",), echo=print) -> dict:
    data = yaml.safe_load(Path(workflow_path).read_text(encoding="utf-8"))
    declared = data[True if True in data else "on"]["workflow_call"]["inputs"]
    merged = {name: spec.get("default") for name, spec in declared.items()}
    merged.update(inputs)
    missing = [name for name, spec in declared.items() if spec.get("required") and merged.get(name) in (None, "")]
    if missing:
        raise SystemExit(f"thiếu input bắt buộc: {', '.join(missing)}")
    ctx = Context(merged, secrets, github)
    steps = data["jobs"]["gate"]["steps"]
    bash, failed, results = find_bash(), False, {}
    with tempfile.TemporaryDirectory() as tmp:
        output_file = Path(tmp) / "github_output"
        for step in steps:
            name = step.get("name") or step.get("uses", "?")
            if "run" not in step:
                echo(f"[skip] {name} (uses:)")
                continue
            if name in skip:
                echo(f"[skip] {name}")
                continue
            condition = str(step.get("if", ""))
            if failed and "always()" not in condition:
                echo(f"[skip] {name} (bước trước lỗi)")
                continue
            output_file.write_text("", encoding="utf-8")
            env = {**os.environ, "MSYS_NO_PATHCONV": "1", "GITHUB_OUTPUT": str(output_file)}
            env.update({k: str(v) for k, v in github.get("env", {}).items()})
            env.update({k: ctx.render(v) for k, v in (step.get("env") or {}).items()})
            echo(f"[run ] {name}")
            proc = subprocess.run([bash, "-e", "-c", step["run"]], cwd=workspace, env=env, text=True, encoding="utf-8",
                                  capture_output=True)
            for line in (proc.stdout + proc.stderr).splitlines()[-25:]:
                echo("       " + line)
            outputs = {}
            for line in output_file.read_text(encoding="utf-8").splitlines():
                if "=" in line:
                    key, _, value = line.partition("=")
                    outputs[key] = value
            if step.get("id"):
                ctx.steps[step["id"]] = {"outputs": outputs, "outcome": "success" if proc.returncode == 0 else "failure"}
            results[name] = {"returncode": proc.returncode, "outputs": outputs}
            if proc.returncode != 0:
                failed = True
    return results


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--workflow", default=str(DEFAULT_WORKFLOW))
    ap.add_argument("--workspace", required=True, help="thư mục repo SUT (có Dockerfile và .qc-agent/suites)")
    ap.add_argument("--input", action="append", default=[], metavar="K=V")
    ap.add_argument("--secret", action="append", default=[], metavar="K=V")
    ap.add_argument("--github-api", default="https://api.github.com")
    ap.add_argument("--repository", default="o/r")
    ap.add_argument("--sha", default="abc1234def5678")
    ap.add_argument("--pr", type=int, default=7)
    ap.add_argument("--token", default="ghs_local_test_token")
    args = ap.parse_args(argv)
    kv = lambda items: dict(item.split("=", 1) for item in items)  # noqa: E731
    with tempfile.TemporaryDirectory() as tmp:
        event = Path(tmp) / "event.json"
        event.write_text(json.dumps({"pull_request": {"number": args.pr, "head": {"sha": args.sha, "ref": "feat/x"}}}), encoding="utf-8")
        github = {"token": args.token, "sha": "mergecommit0000", "actor": "tester", "run_attempt": "1",
                  "event": {"pull_request": {"number": args.pr, "head": {"sha": args.sha}}},
                  "env": {"GITHUB_EVENT_PATH": str(event), "GITHUB_REPOSITORY": args.repository, "GITHUB_SHA": "mergecommit0000",
                          "GITHUB_RUN_ID": "1001", "GITHUB_RUN_ATTEMPT": "1", "GITHUB_SERVER_URL": "https://github.com",
                          "GITHUB_API_URL": args.github_api, "GITHUB_REF_NAME": "feat/x"}}
        results = run_workflow(args.workflow, args.workspace, kv(args.input), kv(args.secret), github)
    return results.get("Enforce gate result", {}).get("returncode", 1)


if __name__ == "__main__":
    sys.exit(main())
