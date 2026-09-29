"""Suite của khâu Security (Làn A): sast (Semgrep), secrets (gitleaks), deps (Trivy). Mẫu ở tmpl/{sast,secrets,deps}.yaml.tmpl.

`init.build()` gọi `add_suites(add, opts)` một lần: `add(rel_path, content)` ghi một file vào repo SUT, `opts` là `init.Options` (chưa dùng: cả ba suite
không phụ thuộc cấu trúc repo ngoài `scan_paths`). Chỗ trống do đây điền phải đi qua bộ làm sạch của templates.py (JSON-quote, đường dẫn an toàn), không nối chuỗi thô.
"""
from __future__ import annotations

from qc_agent.scaffold import templates as t


def sast_suite(*, scan_paths: tuple[str, ...] = (".",), verify: str | None = None) -> str:
    """`scan_paths` = thư mục nguồn cần quét (tương đối repo SUT, không `..`). `verify` (lý do) => TODO VERIFY, chỉ dùng khi đường dẫn là do scanner ĐOÁN."""
    if not scan_paths:
        raise t.TemplateError("scan_paths không được rỗng")
    paths = [t._rel_path(p, "scan_paths") for p in scan_paths]
    note = f"    # {t.todo_mark('VERIFY', verify)}" if verify else ""
    return t.render("sast.yaml.tmpl", {"scan_paths": t._q(paths), "verify_note": note})


def secrets_suite() -> str:
    return t.render("secrets.yaml.tmpl", {})


def deps_suite() -> str:
    return t.render("deps.yaml.tmpl", {})


def add_suites(add, opts) -> None:
    from qc_agent.scaffold.init import SUITES_DIR   # import muộn: init.py import module này ở đầu file
    # Scanner (scan.py) chưa tìm thư mục nguồn: quét cả repo (loại node_modules/tests/dist/build trong suite). Đó là mặc định an toàn chứ không phải đoán,
    # nên KHÔNG kèm TODO VERIFY (init đầy đủ tham số phải cho bộ cấu hình sạch dấu TODO); khi scanner có ứng viên thư mục nguồn thì truyền scan_paths + verify.
    add(f"{SUITES_DIR}/sast.yaml", sast_suite())
    add(f"{SUITES_DIR}/secrets.yaml", secrets_suite())
    add(f"{SUITES_DIR}/deps.yaml", deps_suite())
