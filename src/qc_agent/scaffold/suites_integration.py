"""Khung Integration (Làn B): suite integration/integration-live (playwright + bản ghi HAR) và ba file hỗ trợ/spec, MỌI file đuôi `.example`.

`init.build()` gọi `add_suites(add, opts)` một lần: `add(rel_path, content)` ghi một file vào repo SUT, `opts` là `init.Options`.
Không phát hiện được từ cấu trúc repo rằng SUT có cần integration hay không, và nội dung (giao thức, tên check, host) là tri thức riêng của từng SUT,
nên `init` luôn sinh KHUNG TRUNG TÍNH cho mọi SUT nhưng ở dạng chưa hoạt động: `load_suites` và `validate` chỉ đọc `*.yaml`/`*.yml`, nên `.example` bị bỏ qua.
SUT cần thì bỏ đuôi `.example` ở cả 5 file, điền nội dung thật, rồi đăng ký suite trong policy project.
"""
from __future__ import annotations

from pathlib import Path

from qc_agent.scaffold import templates as t

EXAMPLE = ".example"
PLACEHOLDER_B_HOST = "example.invalid"   # file khung chưa hoạt động nên placeholder chấp nhận được; `validate` chặn khi đã kích hoạt mà chưa thay
EXAMPLE_NOTE = ("khung integration đã sinh dạng .example: CHƯA hoạt động và chưa chạy ở PR; muốn dùng thì bỏ đuôi .example ở cả 5 file "
                "(.qc-agent/suites/integration*.yaml, .qc-agent/integration/*.mjs), điền nội dung thật, rồi đăng ký suite integration trong policy project (docs/usage-ci.md)")


def integration_suite(*, b_host: str = PLACEHOLDER_B_HOST) -> str:
    return t.render("integration.yaml.tmpl", {"b_host": t._q(_b_host(b_host)), "todo_line": _suite_todo()})


def integration_live_suite(*, b_host: str = PLACEHOLDER_B_HOST) -> str:
    return t.render("integration-live.yaml.tmpl", {"b_host": t._q(_b_host(b_host)), "todo_line": _suite_todo()})


def support() -> str:
    return t.render("integration-support.mjs.tmpl", {})


def tier1_spec() -> str:
    return t.render("integration-tier1.spec.mjs.tmpl", {"todo": _spec_todo()})


def tier2_spec() -> str:
    return t.render("integration-tier2.spec.mjs.tmpl", {"todo": _spec_todo()})


def _suite_todo() -> str:
    return "# " + _spec_todo()


def _spec_todo() -> str:
    return t.todo_mark("VERIFY", "khung trung tính: viết kiểm tra thật, đổi tên todo_replace_me, thay b_host/HAR bằng giá trị thật rồi xoá dòng này")


def _b_host(value: str) -> str:
    import re
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9](?:[A-Za-z0-9.-]{0,251}[A-Za-z0-9])?", value):
        raise t.TemplateError(f"b_host không hợp lệ: {value!r}")
    return value.lower()


def add_suites(add, opts) -> None:
    from qc_agent.scaffold.init import SUITES_DIR
    root = Path(opts.sut_root)
    files = ((f"{SUITES_DIR}/integration.yaml", integration_suite()),
             (f"{SUITES_DIR}/integration-live.yaml", integration_live_suite()),
             (".qc-agent/integration/support.mjs", support()),
             (".qc-agent/integration/tier1.spec.mjs", tier1_spec()),
             (".qc-agent/integration/tier2.spec.mjs", tier2_spec()))
    for path, content in files:
        if not (root / path).exists():   # bản đã kích hoạt (không đuôi) thì không sinh lại khung bên cạnh nó
            add(path + EXAMPLE, content)
