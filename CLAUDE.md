# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

- Communication Style: Always explain concepts using the "Visualization -> Hands-on Code/Action -> Deep Dive Theory" approach. Prioritize practice over lengthy theory.

Docs and comments are in Vietnamese; match that when editing docs.

## Commands

```bash
pip install uv && uv sync            # .venv + editable qc-agent from uv.lock (requirements.txt mentions in docs/core-rules.md are stale)
npm ci                               # Midscene CLI + Playwright (needed for tests/test_web.py)
pytest -q                            # all tests (pythonpath = . src)
pytest tests/test_runner.py -q       # one file
pytest tests/test_engine.py::test_x  # one test
```

- DB-backed tests (`test_jobs_db.py`, `test_executor.py`, `test_api.py`, `test_web.py`) need `QC_TEST_DATABASE_URL` (`docker compose up -d postgres` gives `postgresql://qc:qc-dev-only@127.0.0.1:5433/...`).
- Demo gate: `qc-agent --plan tests/fixtures/plans/demo.yaml` must exit 0; `demo_fail.yaml` must exit 1. Fake workers need `QC_WORKERS_PATH="workers;tests/fixtures/workers"` (`:` separator on Linux).
- Per-project run: `qc-agent run --project noteboard --mode pr --sut-root tests/fixtures/sut/noteboard` (needs `APP_BASE_URL`; reference SUT is the toy app under `tests/fixtures/sut/noteboard`, started with `python -m uvicorn --app-dir tests/fixtures/sut/noteboard toyapp.app:app --port 8000`).
- Service: `python -m qc_agent.jobs.migrate upgrade` → `python -m qc_agent.jobs.executor` and `uvicorn qc_agent.api.app:create_app --factory --port 8080` (needs `QC_DATABASE_URL`, `QC_ALLOWED_EMAIL_DOMAINS`; `QC_COOKIE_SECURE=false` for plain http). Users/tokens: `qc-agent user add|reset|deactivate|list`, `qc-agent token create --project X --name ci`.
- Onboarding a new SUT repo: one `docker run … init` command (`docs/onboarding.md`); reusable CI gate: `docs/usage-ci.md`; test the workflow locally with `tools/run_reusable_locally.py`.
- Contract check: `python tools/freeze_contract.py --check` must exit 0.
- Env config lives in `src/qc_agent/settings.py` (`QC_RUNS_DIR`, `QC_WORKERS_PATH`, `QC_SCHEMAS_DIR`, `QC_LOG_FORMAT`, …); see `.env.example`.

No linter/formatter is configured. Gate exit codes: PASS 0 · YELLOW = `--yellow-exit` (default 0) · FAIL 1 · plan/system error 3.

## Architecture

Read `docs/architecture.md` first (two nested loops), then `docs/core-rules.md`. Short version:

- **Inner loop (gate, deterministic, blocks merge)**: `core/engine.py` runs plan → resolve → pick worker by capability → run → verdict → report. Policy comes from `configs/projects/_default.yaml` deep-merged (lists replace) with optional `<slug>.yaml`; the SUT's approved suites live in its own repo at `.qc-agent/suites/` and workers run with cwd = SUT root.
- **Outer loop (LLM, advisory)**: generates/repairs tests and proposes them as PRs for human review (`scaffold/refine.py`, `integrations/refine_review.py`). Connected to the gate only through frozen, human-approved files and a test-debt ledger in Postgres. Diff-based test selection may only *narrow* runs within the approved suite, and must run everything when it can't map a change.
- **Workers**: `workers/<name>.yaml` manifest + `src/qc_agent/adapters/<name>_adapter.py` (subclass `Adapter` in `_base.py`; only override `build_cmd` and `parse_output`) + optional `src/qc_agent/oracle/<kind>.py` registered with `@register`. Workers (schemathesis, k6, midscene, deepeval) are spawned as subprocesses and speak the shared `schemas/task_spec.json` / `result.json` contract; capabilities are listed in `schemas/capabilities.json`. Adding a worker = adding files, never editing `core/`.
- **Service layer**: `api/` (FastAPI, `/api/v1`, also serves the static `web/` SPA — API is primary, web is just a client), `jobs/` (SQLAlchemy + Alembic models/migrations, `executor.py` runs jobs by shelling out to the CLI with cancel/timeout/env-lock/requeue), `auth/` (admin-invited users, argon2, lockout), `integrations/` (GitHub Check Runs/comments, webhook notify, CI ingest). `scaffold/` implements `init` (deterministic scanner + templates) and `init --refine`.
- One Docker image serves CLI, API and executor (`Dockerfile`); `Dockerfile.ui.tmpl` is for SPA SUTs.

## Hard rules (from docs/core-rules.md)

- No LLM calls and no `if worker == ...` in `core/`; per-worker differences belong in manifest/adapter/oracle. LLMs may only appear in workers, and only for non-blocking findings.
- No worker-specific fields in the shared schemas.
- Retry only `error` (infra), once. Never retry `fail`.
- Do not edit contract files (`schemas/task_spec.json`, `schemas/result.json`, `workers/_template.yaml`) outside the SemVer process guarded by `schemas/CONTRACT.lock`, `tools/freeze_contract.py` and the `contract-check` workflow (reviewers: `.github/contract-reviewers.yaml`).
- Logs are structured JSON on stderr and must never include spec/inputs content or secrets (`logging_setup.py`); off-machine data flow is recorded per worker-declared `data_egress` in `runs/<job>/egress.jsonl`.
- In plan YAML, quote the retry key `"on":` (bare `on` parses as `True`).
- Run from the repo root: adapters are spawned with `python -m` and plan paths are relative. Base images and GitHub Actions are pinned by digest/SHA; keep them pinned.
