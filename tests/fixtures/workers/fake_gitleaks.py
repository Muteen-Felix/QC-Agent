"""Adapter gitleaks thật với công cụ giả (xem fake_security.py). Chạy bằng `python -m tests.fixtures.workers.fake_gitleaks`."""
from qc_agent.adapters.gitleaks_adapter import GitleaksAdapter
from tests.fixtures.workers.fake_security import fake_adapter

Fake = fake_adapter(GitleaksAdapter, "gitleaks")

if __name__ == "__main__":
    raise SystemExit(Fake().main())
