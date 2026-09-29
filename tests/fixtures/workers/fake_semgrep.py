"""Adapter semgrep thật với công cụ giả (xem fake_security.py). Chạy bằng `python -m tests.fixtures.workers.fake_semgrep`."""
from qc_agent.adapters.semgrep_adapter import SemgrepAdapter
from tests.fixtures.workers.fake_security import fake_adapter

Fake = fake_adapter(SemgrepAdapter, "semgrep")

if __name__ == "__main__":
    raise SystemExit(Fake().main())
