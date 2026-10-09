"""Kiểm image qc-agent mà harness local (tools/run_reusable_locally.py, tests Docker) sắp dùng có được build từ commit đang thử không (S4-05).

Vì sao cần: harness chạy workflow bằng `docker run <image>`; image cũ vẫn chạy được nhưng kiểm mã CŨ, nên test xanh mà không chứng minh gì về commit này.

  Lớp 1 (mặc định)   `QC_AGENT_GIT_SHA` trong image == `git rev-parse HEAD`, và cây làm việc không đổi ở các đường dẫn đầu vào của image.
                     `unknown`/rỗng (build thiếu --build-arg) hoặc lệch hoặc cây bẩn => StaleImage, kèm lệnh build đúng.
  Lớp 2 (`QC_HARNESS_IMAGE_CHECK=content`)   so băm `qc_agent/**/*.py` trong image với `src/qc_agent/**/*.py` trên máy, cho vòng lặp khi chưa commit.
                     GIỚI HẠN: chỉ mã Python của gói; không thấy thay đổi ở schemas/workers/configs/rules/Dockerfile/phụ thuộc.
  Lối thoát          `QC_HARNESS_ALLOW_STALE_IMAGE=1`: cho chạy, cảnh báo to, kết quả mang nhãn "IMAGE CHƯA XÁC MINH"; không dùng để nghiệm thu.
  DB Trivy           `trivy_db_age_days` / `check_trivy_db`: suite `deps` chặn khi DB > 14 ngày; build có cache vẫn có thể ra DB cũ (đã đo ở spike S4-05).

    python tools/image_check.py --image qc-agent:harness-abc1234 [--db]      # exit 0 đạt · 1 StaleImage/StaleTrivyDb · 3 không chạy được docker/image
"""
from __future__ import annotations

import argparse
import hashlib
import os
import shutil
import subprocess
import sys
import tempfile
import warnings
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# Đường dẫn đầu vào của image (Dockerfile COPY chúng, hoặc chúng quyết định lớp phụ thuộc). Cây bẩn ở đây => SHA không chứng minh được nội dung image.
INPUT_PATHS = ("src", "schemas", "workers", "configs", "web", "rules", "docker", "pyproject.toml", "uv.lock", "package.json", "package-lock.json", "Dockerfile")
ENV_ALLOW = "QC_HARNESS_ALLOW_STALE_IMAGE"
ENV_MODE = "QC_HARNESS_IMAGE_CHECK"
ENV_IMAGE = "QC_TEST_DOCKER_IMAGE"
TRIVY_CACHE_DIR = "/opt/trivy-cache"
MAX_DB_AGE_DAYS = 14   # khớp ngưỡng `trivy.db_age_days <= 14` của suite deps (scaffold/tmpl/deps.yaml.tmpl)
WARN_DB_AGE_DAYS = 12
TLS_MARKERS = ("UNABLE_TO_GET_ISSUER_CERT_LOCALLY", "ENOTFOUND", "ECONNRESET", "certificate", "x509", "TLS", "timed out")


class ImageCheckError(Exception):
    """Không kiểm được (không có docker, không có image, build lỗi): khác với image sai."""


class StaleImage(ImageCheckError):
    """Image không chứng minh được là build từ commit đang thử."""


class StaleTrivyDb(ImageCheckError):
    """DB CVE nướng trong image quá tuổi cho suite `deps`."""


class ImageWarning(UserWarning):
    pass


@dataclass(frozen=True)
class Verdict:
    image: str
    verified: bool
    mode: str                # "commit" | "content"
    image_commit: str
    head: str
    problems: tuple = ()

    @property
    def label(self) -> str:
        if self.verified:
            return f"IMAGE ĐÃ XÁC MINH ({self.mode}; image {self.image_commit[:7] or '-'}, HEAD {self.head[:7]})"
        return "IMAGE CHƯA XÁC MINH: " + "; ".join(self.problems)


def _run(args, *, cwd=None, timeout=300):
    """Một chỗ duy nhất gọi tiến trình ngoài: test thay bằng `docker` giả."""
    return subprocess.run(args, cwd=cwd, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=timeout, check=False)


def source_commit(repo: Path = ROOT) -> str:
    done = _run(["git", "rev-parse", "HEAD"], cwd=repo)
    if done.returncode != 0:
        raise ImageCheckError("không đọc được `git rev-parse HEAD`: " + done.stderr.strip())
    return done.stdout.strip()


def dirty_inputs(repo: Path = ROOT) -> list[str]:
    """Các dòng `git status --porcelain` (kể cả file chưa theo dõi) ở đường dẫn đầu vào của image."""
    done = _run(["git", "status", "--porcelain", "--untracked-files=all", "--", *INPUT_PATHS], cwd=repo)
    if done.returncode != 0:
        raise ImageCheckError("không đọc được `git status`: " + done.stderr.strip())
    return [line for line in done.stdout.splitlines() if line.strip()]


def build_command(head: str, *, refresh_trivy: bool = False) -> str:
    # Đã đo (S4-05): `--no-cache-filter trivy` chạy lại stage nhưng image cuối vẫn giữ DB cũ (hai lần); `--no-cache` mới cho DB mới.
    cache = "--no-cache " if refresh_trivy else ""
    return f"docker build {cache}--build-arg QC_AGENT_GIT_SHA=$(git rev-parse HEAD) -t qc-agent:harness-{head[:7]} ."


def image_commit(image: str) -> str:
    """Giá trị `QC_AGENT_GIT_SHA` trong image; chuỗi rỗng nếu image không đặt biến (vd. `node:22-bookworm-slim`)."""
    done = _run(["docker", "run", "--rm", "--entrypoint", "printenv", image, "QC_AGENT_GIT_SHA"], timeout=120)
    if done.returncode == 0:
        return done.stdout.strip()
    if done.returncode == 1 and not done.stderr.strip():
        return ""   # printenv thoát 1 khi biến không tồn tại
    raise ImageCheckError(f"không chạy được image {image!r}: {(done.stderr or done.stdout).strip()[:300]}")


def _hash_files(files) -> str:
    digest = hashlib.sha256()
    for rel, data in files:
        digest.update(rel.encode() + b"\0" + data.replace(b"\r\n", b"\n") + b"\0")   # CRLF -> LF: autocrlf trên Windows không được làm lệch băm
    return digest.hexdigest()


def local_content_hash(repo: Path = ROOT) -> str:
    base = repo / "src" / "qc_agent"
    files = sorted((p.relative_to(base).as_posix(), p) for p in base.rglob("*.py") if "__pycache__" not in p.parts)
    return _hash_files((rel, path.read_bytes()) for rel, path in files)


# Chạy TRONG image: cùng thuật toán với local_content_hash (cùng thứ tự, cùng chuẩn hoá CRLF).
IMAGE_HASH_SCRIPT = (
    "import hashlib, pathlib, qc_agent\n"
    "base = pathlib.Path(qc_agent.__file__).resolve().parent\n"
    "files = sorted((p.relative_to(base).as_posix(), p) for p in base.rglob('*.py') if '__pycache__' not in p.parts)\n"
    "d = hashlib.sha256()\n"
    "for rel, p in files:\n"
    "    d.update(rel.encode() + b'\\0' + p.read_bytes().replace(b'\\r\\n', b'\\n') + b'\\0')\n"
    "print(d.hexdigest())\n"
)


def image_content_hash(image: str) -> str:
    done = _run(["docker", "run", "--rm", "--entrypoint", "python", image, "-c", IMAGE_HASH_SCRIPT], timeout=120)
    if done.returncode != 0:
        raise ImageCheckError(f"không băm được mã trong image {image!r}: {(done.stderr or done.stdout).strip()[:300]}")
    return done.stdout.strip()


def _problems_commit(image: str, repo: Path) -> tuple[str, str, list[str]]:
    head = source_commit(repo)
    got = image_commit(image)
    problems = []
    if got in ("", "unknown"):
        problems.append(f"image không có dấu commit (QC_AGENT_GIT_SHA={got or '<không đặt>'}): build thiếu --build-arg QC_AGENT_GIT_SHA")
    elif got != head:
        problems.append(f"image build từ {got[:7]}, HEAD là {head[:7]}")
    dirty = dirty_inputs(repo)
    if dirty:
        problems.append(f"cây làm việc có {len(dirty)} thay đổi chưa commit ở đầu vào của image (vd. {dirty[0].strip()}): SHA không chứng minh được nội dung")
    return head, got, problems


def verify(image: str, *, repo: Path = ROOT, env=None) -> Verdict:
    """Image đạt => Verdict(verified=True). Không đạt => StaleImage, trừ khi QC_HARNESS_ALLOW_STALE_IMAGE=1 (Verdict(verified=False), cảnh báo to)."""
    env = os.environ if env is None else env
    mode = (env.get(ENV_MODE) or "commit").strip().lower()
    if mode not in ("commit", "content"):
        raise ImageCheckError(f"{ENV_MODE} chỉ nhận commit|content, nhận {mode!r}")
    if mode == "content":
        head = source_commit(repo)
        got = image_commit(image)
        problems = []
        if image_content_hash(image) != local_content_hash(repo):
            problems.append("băm qc_agent/**/*.py trong image khác src/qc_agent/**/*.py trên máy")
    else:
        head, got, problems = _problems_commit(image, repo)
    if not problems:
        return Verdict(image, True, mode, got, head)
    hint = f"Build lại: {build_command(head)}  (hoặc {ENV_MODE}=content khi đang sửa mã Python, hoặc {ENV_ALLOW}=1 chỉ để phát triển, không nghiệm thu)"
    if (env.get(ENV_ALLOW) or "").strip() == "1":
        verdict = Verdict(image, False, mode, got, head, tuple(problems))
        warnings.warn(f"{verdict.label}. {ENV_ALLOW}=1: KẾT QUẢ KHÔNG DÙNG ĐỂ NGHIỆM THU. {hint}", ImageWarning, stacklevel=2)
        return verdict
    raise StaleImage(f"image {image!r} không chứng minh được là build từ commit đang thử: " + "; ".join(problems) + ". " + hint)


def trivy_db_age_days(image: str) -> int:
    """Tuổi DB CVE trong image, tính BẰNG ĐÚNG hàm của adapter (`trivy_adapter.db_age_days`: UpdatedAt, làm tròn lên) trên metadata.json lấy ra từ image."""
    try:
        from qc_agent.adapters import trivy_adapter
    except ImportError:
        sys.path.insert(0, str(ROOT / "src"))
        from qc_agent.adapters import trivy_adapter
    done = _run(["docker", "run", "--rm", "--entrypoint", "cat", image, f"{TRIVY_CACHE_DIR}/db/metadata.json"], timeout=120)
    if done.returncode != 0:
        raise ImageCheckError(f"image {image!r} không có DB Trivy ({TRIVY_CACHE_DIR}/db/metadata.json): {(done.stderr or done.stdout).strip()[:200]}")
    with tempfile.TemporaryDirectory() as tmp:
        (Path(tmp) / "db").mkdir()
        (Path(tmp) / "db" / "metadata.json").write_text(done.stdout, encoding="utf-8")
        try:
            return trivy_adapter.db_age_days(tmp)
        except Exception as error:   # AdapterParseError: metadata hỏng
            raise ImageCheckError(f"metadata DB Trivy của image {image!r} không đọc được: {error}") from error


def check_trivy_db(image: str, *, deps_expected: bool) -> int:
    """`deps_expected=True` mà DB quá 14 ngày => StaleTrivyDb (lỗi cứng, TRƯỚC khi chạy Gate). `False` chỉ cảnh báo. Trả về tuổi DB (ngày)."""
    age = trivy_db_age_days(image)
    head = source_commit()
    if age > MAX_DB_AGE_DAYS:
        message = (f"DB CVE của image {image!r} {age} ngày > {MAX_DB_AGE_DAYS}: suite `deps` sẽ đỏ vì trivy.db_age_days. Build lại KHÔNG dùng cache: "
                   f"{build_command(head, refresh_trivy=True)}; rồi đọc lại `python tools/image_check.py --image <image> --db` (`--no-cache-filter trivy` đã đo là không làm mới DB)")
        if deps_expected:
            raise StaleTrivyDb(message)
        warnings.warn(message + " (kịch bản này không chạy deps nên chỉ cảnh báo)", ImageWarning, stacklevel=2)
    elif age >= WARN_DB_AGE_DAYS:
        warnings.warn(f"DB CVE của image {image!r} {age} ngày: sắp quá {MAX_DB_AGE_DAYS} ngày, suite `deps` sẽ đỏ", ImageWarning, stacklevel=2)
    return age


def docker_available() -> bool:
    return shutil.which("docker") is not None


def _image_exists(image: str) -> bool:
    return _run(["docker", "image", "inspect", image], timeout=60).returncode == 0


def ensure_image(*, build: bool = True, env=None, repo: Path = ROOT) -> Verdict:
    """Image để chạy harness, ĐÃ ĐƯỢC KIỂM: QC_TEST_DOCKER_IMAGE, hoặc `qc-agent:harness-<sha7>` nếu có, hoặc build từ HEAD.
    Build lỗi mạng/TLS thì DỪNG kèm hướng dẫn (không skip im lặng)."""
    env = os.environ if env is None else env
    if not docker_available():
        raise ImageCheckError("không có docker trong PATH")
    named = (env.get(ENV_IMAGE) or "").strip()
    if named:
        return verify(named, repo=repo, env=env)
    head = source_commit(repo)
    tag = f"qc-agent:harness-{head[:7]}"
    if not _image_exists(tag):
        if not build:
            raise ImageCheckError(f"không có image {tag}; đặt {ENV_IMAGE} hoặc build: {build_command(head)}")
        done = _run(["docker", "build", "--build-arg", f"QC_AGENT_GIT_SHA={head}", "-t", tag, "."], cwd=repo, timeout=3600)
        if done.returncode != 0:
            tail = (done.stderr or done.stdout)[-1500:]
            if any(marker in tail for marker in TLS_MARKERS):
                raise ImageCheckError("docker build lỗi mạng/TLS (thường gặp ở mạng công ty khi tải Chromium). Thử lại MỘT lần (các lớp đã cache); nếu vẫn lỗi, "
                                      f"dùng image dựng sẵn ở mạng khác: đặt {ENV_IMAGE}=<image> (vẫn bị kiểm). Cuối log:\n{tail}")
            raise ImageCheckError("docker build lỗi:\n" + tail)
    return verify(tag, repo=repo, env=env)


def main(argv=None) -> int:
    for stream in (sys.stdout, sys.stderr):   # Windows: console cp1252 không in được tiếng Việt
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(errors="replace")
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--image", help=f"image cần kiểm (mặc định: {ENV_IMAGE} hoặc qc-agent:harness-<sha7>)")
    ap.add_argument("--db", action="store_true", help="kiểm cả tuổi DB Trivy như khi kịch bản chạy deps (lỗi nếu > 14 ngày)")
    args = ap.parse_args(argv)
    try:
        if args.image:
            verdict = verify(args.image)
        else:
            verdict = ensure_image(build=False)
        print(verdict.label)
        if args.db:
            print(f"DB Trivy: {check_trivy_db(verdict.image, deps_expected=True)} ngày (ngưỡng {MAX_DB_AGE_DAYS})")
        return 0 if verdict.verified else 1
    except (StaleImage, StaleTrivyDb) as error:
        print(f"LỖI: {error}", file=sys.stderr)
        return 1
    except (ImageCheckError, subprocess.TimeoutExpired) as error:
        print(f"KHÔNG KIỂM ĐƯỢC: {error}", file=sys.stderr)
        return 3


if __name__ == "__main__":
    sys.exit(main())
