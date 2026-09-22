"""core/cli + orchestrator.py: chạy đúng đường thật (subprocess -> adapter mock) và kiểm exit code.
Mỗi lần chạy tốn ~1s vì spawn worker thật; đó là chủ ý — cli là điểm ghép nên không mock gì cả."""
import json
import os
import subprocess
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
DEMO = ROOT / "plans" / "demo.yaml"
DEMO_FAIL = ROOT / "plans" / "demo_fail.yaml"


def run_cli(tmp_path, *args):
    env = {k: v for k, v in os.environ.items() if k != "QC_RUNS_DIR"}
    env["PYTHONUTF8"] = "1"
    return subprocess.run(
        [sys.executable, "orchestrator.py", *args, "--runs-dir", str(tmp_path / "runs")],
        cwd=ROOT, env=env, capture_output=True, text=True, encoding="utf-8", timeout=120)


def report_json(tmp_path, run_id="r-0001"):
    return json.loads((tmp_path / "runs" / run_id / "report.json").read_text(encoding="utf-8"))


def write_plan(tmp_path, mutate):
    """Sao demo.yaml sang tmp_path và cho `mutate(tasks)` sửa danh sách task. Trả về đường dẫn plan mới."""
    plan = yaml.safe_load(DEMO.read_text(encoding="utf-8"))
    mutate(plan["tasks"])
    path = tmp_path / "plan.yaml"
    path.write_text(yaml.safe_dump(plan, allow_unicode=True, sort_keys=False), encoding="utf-8")
    return path


def test_01_demo_plan_exits_0_and_writes_report(tmp_path):
    proc = run_cli(tmp_path, "--plan", str(DEMO))
    assert proc.returncode == 0, proc.stderr
    md = (tmp_path / "runs" / "r-0001" / "report.md").read_text(encoding="utf-8")
    assert [f"## {n}." in md for n in range(1, 6)] == [True] * 5
    assert "## VERDICT: ✅ PASS" in proc.stdout  # report cũng được in ra console
    data = report_json(tmp_path)
    assert (data["gate_verdict"], data["exit_code"]) == ("PASS", 0)
    assert (tmp_path / "runs" / "r-0001" / "sut_identity.json").is_file()


def test_02_failing_plan_exits_1(tmp_path):
    proc = run_cli(tmp_path, "--plan", str(DEMO_FAIL))
    assert proc.returncode == 1, proc.stderr
    data = report_json(tmp_path)
    assert (data["gate_verdict"], data["exit_code"]) == ("FAIL", 1)
    by_task = {row["task_id"]: row["value"] for row in data["deterministic_view"]}
    assert by_task == {"t-e01": "pass", "t-e02": "fail"}  # t-e03 (discovery) không nằm trong deterministic_view


def test_03_invalid_plan_exits_3(tmp_path):
    def unknown_capability(tasks):
        tasks[0]["capability"] = "khong.ton_tai"

    proc = run_cli(tmp_path, "--plan", str(write_plan(tmp_path, unknown_capability)))
    assert proc.returncode == 3
    assert "khong.ton_tai" in proc.stderr
    assert not (tmp_path / "runs").exists()  # lỗi plan xảy ra trước khi tạo thư mục run


def test_04_only_runs_a_subset(tmp_path):
    proc = run_cli(tmp_path, "--plan", str(DEMO), "--only", "t-e01")
    assert proc.returncode == 0, proc.stderr
    assert [row["task_id"] for row in report_json(tmp_path)["deterministic_view"]] == ["t-e01"]
    assert sorted(p.stem for p in (tmp_path / "runs" / "r-0001" / "results").glob("*.json")) == ["t-e01"]


def test_05_only_without_its_dependency_exits_3(tmp_path):
    def chain(tasks):
        tasks[1]["depends_on"] = ["t-e01"]

    plan = str(write_plan(tmp_path, chain))
    proc = run_cli(tmp_path, "--plan", plan, "--only", "t-e02")
    assert proc.returncode == 3
    assert "t-e01" in proc.stderr
    assert run_cli(tmp_path, "--plan", plan, "--only", "t-e01,t-e02").returncode == 0


def test_06_only_with_unknown_task_exits_3(tmp_path):
    proc = run_cli(tmp_path, "--plan", str(DEMO), "--only", "t-zzz")
    assert proc.returncode == 3
    assert "t-zzz" in proc.stderr


def test_07_yellow_exit_flag(tmp_path):
    def skipped_gate_task(tasks):  # capability hợp lệ nhưng không worker nào nhận ⟹ skipped ở gate lane ⟹ YELLOW
        tasks.append({**tasks[0], "task_id": "t-e99", "capability": "http.load"})

    plan = str(write_plan(tmp_path, skipped_gate_task))
    default = run_cli(tmp_path, "--plan", plan)
    assert default.returncode == 0, default.stderr
    assert report_json(tmp_path)["gate_verdict"] == "YELLOW"
    assert "SKIPPED / ERROR — ĐỌC TRƯỚC" in default.stdout

    blocking = run_cli(tmp_path, "--plan", plan, "--yellow-exit", "2")
    assert blocking.returncode == 2
    assert report_json(tmp_path, "r-0002")["exit_code"] == 2  # report.json khớp exit của tiến trình


def test_08_usage_error_exits_3_not_argparse_2(tmp_path):
    # exit 2 là giá trị CI có thể đặt cho YELLOW (--yellow-exit 2): lỗi cú pháp không được trùng
    assert run_cli(tmp_path).returncode == 3  # thiếu --plan
    assert run_cli(tmp_path, "--plan", str(DEMO), "--yellow-exit", "abc").returncode == 3
    assert run_cli(tmp_path, "--plan", str(tmp_path / "khong-co.yaml")).returncode == 3


def test_09_run_ids_increment_from_the_highest_existing(tmp_path):
    assert run_cli(tmp_path, "--plan", str(DEMO), "--only", "t-e01").returncode == 0
    assert run_cli(tmp_path, "--plan", str(DEMO), "--only", "t-e01").returncode == 0
    assert sorted(p.name for p in (tmp_path / "runs").iterdir()) == ["r-0001", "r-0002"]


def test_10_rerender_recomputes_without_touching_the_original_report(tmp_path):
    assert run_cli(tmp_path, "--plan", str(DEMO_FAIL)).returncode == 1
    run_dir = tmp_path / "runs" / "r-0001"
    original = (run_dir / "report.md").read_bytes()

    proc = run_cli(tmp_path, "--rerender", str(run_dir))
    assert proc.returncode == 1, proc.stderr  # verdict được tính lại từ file, vẫn đỏ
    rerendered = (run_dir / "report.rerender.md").read_text(encoding="utf-8")
    verdict = [line for line in rerendered.splitlines() if line.startswith("## VERDICT")]
    assert verdict == ["## VERDICT: ❌ FAIL"]
    assert (run_dir / "report.md").read_bytes() == original
    assert not (tmp_path / "runs" / "r-0002").exists()  # không tạo run mới, không chạy worker


def test_11_rerender_follows_the_stored_results(tmp_path):
    assert run_cli(tmp_path, "--plan", str(DEMO_FAIL)).returncode == 1
    run_dir = tmp_path / "runs" / "r-0001"
    result_file = run_dir / "results" / "t-e02.json"
    result = json.loads(result_file.read_text(encoding="utf-8"))
    result.update(status="pass")
    result["verdict"]["value"] = "pass"
    result_file.write_text(json.dumps(result, ensure_ascii=False), encoding="utf-8")

    assert run_cli(tmp_path, "--rerender", str(run_dir)).returncode == 0  # đọc đúng file result, không chạy lại worker


def test_12_rerender_of_a_non_run_dir_exits_3(tmp_path):
    assert run_cli(tmp_path, "--rerender", str(tmp_path)).returncode == 3
