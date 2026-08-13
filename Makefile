.PHONY: install test lint audit audit-npm audit-all db-up db-down migrate init-db sample-ingest sample-export sample-audit e2e e2e-smoke e2e-up e2e-down e2e-install learn-city-help learn-city-hrm-mainland eval-retrieval-baseline check-retrieval-baseline

DB_URL ?= postgresql+psycopg://layer1:layer1@localhost:5432/layer1

install:
	python -m pip install -e ".[dev]"

test:
	pytest

lint:
	ruff check src tests

audit:
	pip-audit

audit-npm:
	cd web && npm audit --omit=dev

audit-all: audit audit-npm

db-up:
	docker compose up -d postgres

db-down:
	docker compose down

migrate:
	DATABASE_URL="$(DB_URL)" alembic upgrade head

init-db:
	layer1 init-db --db-url "$(DB_URL)"

sample-ingest:
	layer1 ingest tests/fixtures/synthetic_bylaw.txt --db-url "$(DB_URL)" --create-schema --municipality "Sampleton" --bylaw-name "Synthetic Zoning Bylaw"

sample-export:
	layer1 export-json 1 --db-url "$(DB_URL)" --out examples/synthetic_bylaw_export.json

sample-audit:
	layer1 audit-pages 1 --db-url "$(DB_URL)" --sample 2

# --- Agentic learning pipeline -------------------------------------------
# Run with: make learn-city-hrm-mainland
# Or use learn-city-help to see all available flags.

LEARN_CITY_JURISDICTION ?= HRM-MAINLAND
LEARN_CITY_NAME        ?= Halifax Regional Municipality
LEARN_CITY_PROVINCE    ?= Nova Scotia
LEARN_CITY_SEED_URL    ?= https://www.halifax.ca/home-property/planning-development/policies-planning-documents/regional-plan/the-plan-for-each-area-of-hrm/halifax-plan-area
LEARN_CITY_OUTPUT      ?= abs-learning/output/$(LEARN_CITY_JURISDICTION)/manifest.json

learn-city-help:
	layer1 learn-city --help

learn-city-hrm-mainland:
	layer1 learn-city \
	  --jurisdiction-code "$(LEARN_CITY_JURISDICTION)" \
	  --name "$(LEARN_CITY_NAME)" \
	  --province "$(LEARN_CITY_PROVINCE)" \
	  --seed-url "$(LEARN_CITY_SEED_URL)" \
	  --output "$(LEARN_CITY_OUTPUT)"

# --- Instrumented UI tests (Playwright) -----------------------------------
# `make e2e-up` boots the test stack: the DEDICATED ephemeral e2e Postgres
# (compose service postgres-e2e, host :5433 by default — never the dev
# instance on :5432) + uvicorn:8001 + next dev:3001, then migrates and
# seeds a demo user. `make e2e` runs the full Playwright suite end-to-end
# and tears the stack down; `make e2e-down` destroys the e2e Postgres
# container AND its volume, so every run starts from a pristine instance
# (ABS-428). `make e2e-smoke` runs the smoke subset across all viewports.

e2e-install:
	cd web && npm install
	cd web && npx playwright install --with-deps

e2e-up:
	./scripts/e2e-up.sh

e2e-down:
	./scripts/e2e-down.sh

e2e-smoke: e2e-up
	cd web && NEXT_PUBLIC_GENERAL_FEEDBACK_ENABLED=true npx playwright test e2e/smoke
	./scripts/e2e-down.sh

e2e: e2e-up
	cd web && NEXT_PUBLIC_GENERAL_FEEDBACK_ENABLED=true npx playwright test
	./scripts/e2e-down.sh

# --- Retrieval eval baseline (ABS-502) -----------------------------------
# `make eval-retrieval-baseline` is THE documented way to re-record
# evals/retrieval/BASELINE.json. It needs the dev corpus, so it takes an
# explicit DSN rather than the ambient one: a worktree shell set up for a
# parallel e2e run exports DATABASE_URL / PG_PORT pointing at that worktree's
# ephemeral, empty database, and the harness would then measure nothing.
# Override with `make eval-retrieval-baseline EVAL_DB_URL=…`.
#
# `make check-retrieval-baseline` is the gate: it fails when the retrieval code
# has moved and the baseline has not. It needs no database.

EVAL_DB_URL ?= $(DB_URL)
PYTHON ?= $(shell test -x .venv/bin/python && echo .venv/bin/python || echo python)

eval-retrieval-baseline:
	$(PYTHON) scripts/eval_retrieval_recall.py --database-url "$(EVAL_DB_URL)"

check-retrieval-baseline:
	$(PYTHON) scripts/check_retrieval_baseline.py
