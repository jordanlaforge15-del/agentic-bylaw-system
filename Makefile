.PHONY: install lock check-lock test lint audit audit-npm audit-all db-up db-down migrate check-migration-drift init-db sample-ingest sample-export sample-audit e2e e2e-smoke e2e-up e2e-down e2e-install learn-city-help learn-city-hrm-mainland eval-retrieval-baseline check-retrieval-baseline advisor-eval

DB_URL ?= postgresql+psycopg://layer1:layer1@localhost:5432/layer1

# Installs the committed hash-pinned lock, then the project without letting pip
# re-resolve past it (ABS-532). requirements/dev.txt is [dev,advisor]; the old
# `pip install -e ".[dev]"` here resolved pyproject.toml's floors afresh, which
# is one of the five independent resolution points the lock replaced.
install:
	python -m pip install --require-hashes -r requirements/dev.txt
	python -m pip install -e . --no-deps

# Regenerate requirements/*.txt from pyproject.toml. Holds existing pins; use
# `./scripts/lock-python-deps.sh --upgrade` to move versions forward on purpose.
lock:
	./scripts/lock-python-deps.sh

# What CI's lock-drift job runs. Exits 1 when the committed locks no longer
# match pyproject.toml.
check-lock:
	./scripts/lock-python-deps.sh --check

test:
	pytest

lint:
	ruff check src tests

# Audits the pinned set rather than the local venv's resolution, so the result
# describes what actually ships. Matches the dependency-audit workflow.
audit:
	pip-audit --desc on --no-deps -r requirements/dev.txt

audit-npm:
	cd web && npm audit --omit=dev

audit-all: audit audit-npm

db-up:
	docker compose up -d postgres

db-down:
	docker compose down

migrate:
	DATABASE_URL="$(DB_URL)" alembic upgrade head

# Is the target DB behind the migrations on this branch? Exits 1 when it is.
# Run it before a data migration: applying data migrations on top of a pending
# schema migration is the split state ABS-499 exists to surface.
check-migration-drift:
	DATABASE_URL="$(DB_URL)" python scripts/check_migration_drift.py

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

# The one documented way to bring up an advisor for a prompt-corpus eval
# run (ABS-515). Pins the provider and model explicitly instead of
# inheriting them from .env, states the billing mode in a banner, and
# prints the run_test_prompts.py command — with its --allow-metered
# consent flag — to paste in a second shell. Foreground; Ctrl+C stops it.
#   make advisor-eval
#   make advisor-eval ADVISOR_EVAL_MODEL=claude-haiku-4-5 ADVISOR_EVAL_PORT=8010
advisor-eval:
	./scripts/advisor-eval.sh

check-retrieval-baseline:
	$(PYTHON) scripts/check_retrieval_baseline.py
