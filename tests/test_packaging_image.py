"""Image qc-agent mang đủ tài nguyên và đọc/ghi được `test-cases.xlsx` (S4-07). Cần Docker và image build từ commit đang thử (tools/image_check.py, như mọi test harness `docker`):
thiếu Docker/image => SKIP kèm lý do = CHƯA KIỂM CHỨNG (QC_HARNESS_REQUIRE_DOCKER=1 biến skip thành lỗi). Image cũ (StaleImage) luôn là lỗi.

Các lệnh dưới đây KHÔNG dùng tên container/mạng cố định (`docker run --rm` thuần), nên không đụng guard `sut`/`ui`/`db`/`qc-net` của harness và chạy song song được.
Đọc tài nguyên bằng `importlib.resources` từ gói đã cài trong image (`python -I`: không nhận thư mục hiện tại hay PYTHONPATH), không dùng đường dẫn nguồn."""
import json
import os
import shutil
import subprocess
import tomllib
from pathlib import Path

import pytest

from tests import harness_kit as kit
from tests.test_packaging_resources import DECLARED, READ_INSTALLED, REQUIRED_MODULES, REQUIRED_SCHEMAS

pytestmark = pytest.mark.docker
ROOT = kit.ROOT
CATALOG = ROOT / "tests" / "fixtures" / "gt" / "noteboard" / "expected" / ".qc-agent" / "ground-truth"


@pytest.fixture(scope="module")
def image():
    return kit.verified_image().image


def _uid() -> str:
    return f"{os.getuid()}:{os.getgid()}" if hasattr(os, "getuid") else "1000:1000"


def _run(image: str, *args: str, entrypoint: str | None = None, mount: Path | None = None, timeout: int = 300):
    cmd = ["docker", "run", "--rm", "--user", _uid(), "-e", "HOME=/tmp"]
    if mount is not None:
        cmd += ["-v", f"{mount.as_posix()}:/work", "-w", "/work"]
    if entrypoint:
        cmd += ["--entrypoint", entrypoint]
    return subprocess.run([*cmd, image, *args], capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=timeout, check=False)


def test_the_image_reads_every_resource_from_the_installed_package(image):
    wanted = sorted(DECLARED | REQUIRED_MODULES | {f"schemas/{n}" for n in REQUIRED_SCHEMAS} | {"workers/semgrep.yaml", "configs/projects/_default.yaml", "web/index.html"})
    done = _run(image, "-I", "-c", READ_INSTALLED, json.dumps(wanted), entrypoint="python")
    assert done.returncode == 0, done.stderr[-500:]
    result = json.loads(done.stdout)
    assert "site-packages" in result["file"], result["file"]            # bản cài trong /opt/venv, không phải cây nguồn
    assert "groundtruth/prompts/gt_agent.md" in result["sizes"], "thiếu gt_agent.md thì `gt generate --agent` hỏng trong CI"
    assert not {k: v for k, v in result["sizes"].items() if not v}, "có tài nguyên rỗng"
    assert set(result["sizes"]) == set(wanted)


def test_the_installed_version_is_the_one_in_pyproject(image):
    expected = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]["version"]
    done = _run(image, "-I", "-c", "import importlib.metadata as m; print(m.version('qc-agent'))", entrypoint="python")
    assert done.returncode == 0 and done.stdout.strip() == expected, (done.stdout, done.stderr[-300:])


def test_pytest_and_openpyxl_are_main_dependencies_available_in_the_image(image):
    """`pytest` cho worker `pytest` (gate chạy `python -m pytest`), `openpyxl` cho test-cases.xlsx: cả hai nằm ở dependencies chính, không ở nhóm dev."""
    done = _run(image, "-I", "-c", "import openpyxl, pytest; print(openpyxl.__version__, pytest.__version__)", entrypoint="python")
    assert done.returncode == 0, done.stderr[-300:]
    assert _run(image, "-m", "pytest", "--version", entrypoint="python").returncode == 0


def test_the_image_exports_and_reads_back_test_cases_xlsx(image):
    openpyxl = pytest.importorskip("openpyxl", reason="openpyxl không có ở máy chủ: không mở được file xlsx để đối chiếu (SKIP = CHƯA KIỂM CHỨNG)")
    with kit.workspace_dir() as base:
        shutil.copytree(CATALOG, base / ".qc-agent" / "ground-truth")
        for stale in (base / ".qc-agent" / "ground-truth").glob("test-cases.xlsx"):
            stale.unlink()
        exported = _run(image, "gt", "export-xlsx", "--sut-root", "/work", mount=base)
        assert exported.returncode == 0, (exported.stdout + exported.stderr)[-600:]
        workbook = base / ".qc-agent" / "ground-truth" / "test-cases.xlsx"
        assert workbook.is_file() and workbook.stat().st_size > 0
        book = openpyxl.load_workbook(workbook, read_only=True)
        rows = sum(sheet.max_row or 0 for sheet in book.worksheets)
        book.close()
        assert rows > 1, "workbook không có dòng dữ liệu"
        back = _run(image, "gt", "import-xlsx", "--sut-root", "/work", "--dry-run", mount=base)       # đọc lại file vừa ghi: không có thay đổi, exit 0
        assert back.returncode == 0, (back.stdout + back.stderr)[-600:]
