.PHONY: install test lint db-up db-down migrate init-db sample-ingest sample-export

DB_URL ?= postgresql+psycopg://layer1:layer1@localhost:5432/layer1

install:
	python -m pip install -e ".[dev]"

test:
	pytest

lint:
	ruff check src tests

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
