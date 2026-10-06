"""Bước "Start SUT" của workflow tái sử dụng với DB phụ (S4-09), chạy THẬT trên Docker cục bộ qua tools/run_reusable_locally.py: container `db` (Postgres)
rồi SUT mẫu `tests/fixtures/sut/dbapp` đọc DATABASE_URL. CHỈ chạy khi có docker + QC_TEST_DOCKER_IMAGE (image qc-agent đã build, dùng để thăm dò HTTP trong
mạng docker; vd. qc-agent:dev). Image DB mặc định `postgres:17` (đổi bằng QC_TEST_DB_IMAGE), dùng allow_unpinned_image vì test không bịa digest.
Không cần PostgreSQL của repo (QC_TEST_DATABASE_URL). Cần mạng kéo image nền lần đầu; không có Docker thì toàn bộ file này skip."""
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))
import run_reusable_locally as harness  # noqa: E402

IMAGE = os.environ.get("QC_TEST_DOCKER_IMAGE", "")
DB_IMAGE = os.environ.get("QC_TEST_DB_IMAGE", "postgres:17")
FIXTURE = ROOT / "tests" / "fixtures" / "sut" / "dbapp"
POLICY = ROOT / "configs" / "projects"
pytestmark = pytest.mark.skipif(not IMAGE or shutil.which("docker") is None, reason="needs docker + QC_TEST_DOCKER_IMAGE")

MARKER = "MARKER-s3cr3t-9f2c41"
AFTER_START = ("Pull qc-agent image", "Run qc-agent gate", "Report (Check Run, PR comment, history, webhook)", "Upload run artifacts", "Enforce gate result")
GITHUB = {"token": "ghs_local_test_token", "sha": "m", "actor": "t", "run_attempt": "1", "event": {}, "env": {}}
DB_INPUTS = {"project": "noteboard", "image": IMAGE, "allow_unpinned_image": "true", "sut_db_image": DB_IMAGE, "sut_health_path": "/health",
             "sut_db_env": "POSTGRES_DB=app\nPOSTGRES_USER=app\nPOSTGRES_HOST_AUTH_METHOD=password",       # auth bằng mật khẩu để mật khẩu sai bị từ chối
             "sut_db_ready_cmd": "pg_isready -h 127.0.0.1 -U app -d app"}                                    # TCP: bỏ qua máy chủ tạm lúc initdb (chỉ nghe socket)
SECRETS = {"SUT_DB_SECRET_ENV": f"POSTGRES_PASSWORD={MARKER}", "SUT_SECRET_ENV": f"DATABASE_URL=postgresql://app:{MARKER}@db:5432/app\nAPP_TOKEN={MARKER}-tok"}


def db_inputs(**over):
    return {**DB_INPUTS, "sut_dockerfile": "Dockerfile", **over}


def cleanup():
    subprocess.run(["docker", "rm", "-f", "sut", "ui", "db"], capture_output=True)
    subprocess.run(["docker", "network", "rm", "qc-net"], capture_output=True)


@pytest.fixture(autouse=True)
def clean_docker():
    cleanup()
    yield
    cleanup()


def fetch_from_gate_network(url: str) -> str:
    """Đọc URL bằng chính image qc-agent trong qc-net (cách gate nhìn thấy SUT). Mã 503 cũng trả về nội dung."""
    code = ("import sys,urllib.request as u,urllib.error as e\n"
            "try: print(u.urlopen(sys.argv[1], timeout=5).status, end=' ')\n"
            "except e.HTTPError as x: print(x.code, end=' ')\n")
    return subprocess.run(["docker", "run", "--rm", "--network", "qc-net", "--entrypoint", "python", IMAGE, "-c", code, url],
                          capture_output=True, text=True, timeout=60).stdout.strip()


def start(tmp_path, inputs, secrets, *, skip=AFTER_START):
    logs = []
    results = harness.run_workflow(harness.DEFAULT_WORKFLOW, FIXTURE, inputs, secrets, GITHUB, skip=skip, echo=logs.append, policy_dir=POLICY,
                                   runner_temp=tmp_path / "rt")
    return results, "\n".join(logs)


def test_the_sut_that_needs_a_database_goes_green_with_the_db_service_and_the_secrets(tmp_path):
    results, log = start(tmp_path, db_inputs(), SECRETS, skip=(*AFTER_START, "Clean up SUT"))
    assert results["Start SUT"]["returncode"] == 0, log
    assert results["Start SUT"]["outputs"] == {"base_url": "http://sut:8000"}
    assert fetch_from_gate_network("http://sut:8000/health") == "200"                      # SUT nối được DB bằng mật khẩu từ secret
    ps = subprocess.run(["docker", "ps", "--format", "{{.Names}}"], capture_output=True, text=True).stdout.split()
    assert "db" in ps and "sut" in ps
    assert subprocess.run(["docker", "inspect", "-f", "{{range .NetworkSettings.Ports}}{{.}}{{end}}", "db"], capture_output=True, text=True).stdout.strip() in ("", "[]")   # cổng DB không publish
    masks = results["Start SUT"]["masks"]
    assert MARKER in masks and f"postgresql://app:{MARKER}@db:5432/app" in masks and f"{MARKER}-tok" in masks   # đã yêu cầu che từng giá trị
    assert MARKER not in log                                                                 # và không giá trị nào lọt ra log harness
    inspected = subprocess.run(["docker", "inspect", "-f", "{{.Config.Cmd}} {{.Path}} {{.Args}}", "sut", "db"], capture_output=True, text=True).stdout
    assert MARKER not in inspected                                                           # không nằm trên dòng lệnh của container


def test_the_secrets_stay_in_the_container_env_but_the_files_and_containers_are_removed_by_cleanup(tmp_path):
    results, log = start(tmp_path, db_inputs(), SECRETS, skip=AFTER_START)
    assert results["Start SUT"]["returncode"] == 0 and results["Clean up SUT"]["returncode"] == 0, log
    ps = subprocess.run(["docker", "ps", "-a", "--format", "{{.Names}}"], capture_output=True, text=True).stdout.split()
    assert not {"db", "sut", "ui"} & set(ps)
    assert not (tmp_path / "rt" / "sut-secret.env").exists() and not (tmp_path / "rt" / "db-secret.env").exists()
    assert MARKER not in log


def test_a_wrong_database_password_is_rejected_by_the_db_so_the_secret_path_is_really_exercised(tmp_path):
    secrets = {**SECRETS, "SUT_SECRET_ENV": "DATABASE_URL=postgresql://app:wrong-password@db:5432/app"}
    results, log = start(tmp_path, db_inputs(), secrets, skip=AFTER_START)
    assert results["Start SUT"]["returncode"] != 0 and "sut không sẵn sàng sau 120s" in log and "không kết nối được DB" in log


def test_without_the_db_service_the_sut_never_answers_and_start_sut_turns_red_with_a_clear_message(tmp_path):
    inputs = {"project": "noteboard", "image": IMAGE, "allow_unpinned_image": "true", "sut_health_path": "/health"}
    secrets = {"SUT_SECRET_ENV": f"DATABASE_URL=postgresql://app:{MARKER}@db:5432/app"}
    results, log = start(tmp_path, inputs, secrets, skip=("Pull qc-agent image", "Report (Check Run, PR comment, history, webhook)", "Upload run artifacts"))
    assert results["Start SUT"]["returncode"] != 0 and "Run qc-agent gate" not in results
    assert "::error::sut không sẵn sàng sau 120s" in log and "không kết nối được DB" in log     # lỗi của SUT nằm trong log container được in kèm
    assert results["Enforce gate result"]["returncode"] == 1 and MARKER not in log             # job đỏ, không xanh giả
    assert not (tmp_path / "rt" / "sut-secret.env").exists()                                   # cleanup (always) vẫn chạy sau khi Start SUT hỏng


def test_a_db_that_cannot_start_stops_before_the_sut_is_even_built(tmp_path):
    inputs = db_inputs(sut_db_env="POSTGRES_DB=app")                                # thiếu mật khẩu: image Postgres tự thoát
    results, log = start(tmp_path, inputs, {}, skip=("Pull qc-agent image", "Report (Check Run, PR comment, history, webhook)", "Upload run artifacts"))
    assert results["Start SUT"]["returncode"] != 0 and "::error::db không sẵn sàng sau 120s" in log
    assert "POSTGRES_PASSWORD" in log or "Database is uninitialized" in log                      # log của container db được in kèm để biết vì sao
