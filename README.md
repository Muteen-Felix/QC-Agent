> Orchestrator "ngu" là orchestrator đúng. Routing bằng LLM là thừa và có hại.

Đang refactor từ PoC sang `qc-agent` (xem `docs/README.md`). Bản PoC đầy đủ: tag `poc-final`.

- **Chạy orchestrator:** `python orchestrator.py --plan tests/fixtures/plans/demo.yaml` (exit 0), `demo_fail.yaml` (exit 1).
- **Toy app (SUT tham chiếu):** `python -m uvicorn toyapp.app:app --host 127.0.0.1 --port 8000`.
- **Test:** `pytest -q`
