"""Suite của khâu Integration (Làn B): integration (playwright + bản ghi HAR). Bước 0: khung rỗng, B điền ở làn của B.

`init.build()` gọi `add_suites(add, opts)` một lần: `add(rel_path, content)` ghi một file vào repo SUT, `opts` là `init.Options`.
"""
from __future__ import annotations

from pathlib import Path

from qc_agent.scaffold import templates as t


def integration_suite(*, b_host: str = "example.invalid") -> str:
    return t.render("integration.yaml.tmpl", {"b_host": t._q(_b_host(b_host))})


def integration_live_suite(*, b_host: str = "example.invalid") -> str:
    return t.render("integration-live.yaml.tmpl", {"b_host": t._q(_b_host(b_host))})


def support() -> str:
    return t.render("integration-support.mjs.tmpl", {})


def tier1_spec() -> str:
    return t.render("integration-tier1.spec.mjs.tmpl", {
        "todo": t.todo_mark("VERIFY", "xác nhận token, payload filter và cleanup theo môi trường vahan-rpa")})


def tier2_spec() -> str:
    return t.render("integration-tier2.spec.mjs.tmpl", {
        "todo": t.todo_mark("VERIFY", "điền selector và luồng chọn filter → apply → tải report của UI thật")})


def _b_host(value: str) -> str:
    import re
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9](?:[A-Za-z0-9.-]{0,251}[A-Za-z0-9])?", value):
        raise t.TemplateError(f"b_host không hợp lệ: {value!r}")
    return value.lower()


def add_suites(add, opts) -> None:
    rendered = (integration_suite(), integration_live_suite(), support(), tier1_spec(), tier2_spec())
    root = Path(opts.sut_root)
    if not ((root / "vahan-chrome-extension").is_dir()
            or ((root / "apps" / "api-server").is_dir() and (root / "apps" / "web-ui").is_dir())):
        return
    from qc_agent.scaffold.init import SUITES_DIR
    paths = (f"{SUITES_DIR}/integration.yaml", f"{SUITES_DIR}/integration-live.yaml", ".qc-agent/integration/support.mjs",
             ".qc-agent/integration/tier1.spec.mjs", ".qc-agent/integration/tier2.spec.mjs")
    for path, content in zip(paths, rendered, strict=True):
        add(path, content)
