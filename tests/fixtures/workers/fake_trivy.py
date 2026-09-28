"""Adapter trivy thật với công cụ giả (xem fake_security.py). Chạy bằng `python -m tests.fixtures.workers.fake_trivy`."""
from qc_agent.adapters.trivy_adapter import TrivyAdapter
from tests.fixtures.workers.fake_security import fake_adapter

Fake = fake_adapter(TrivyAdapter, "trivy")

if __name__ == "__main__":
    raise SystemExit(Fake().main())
