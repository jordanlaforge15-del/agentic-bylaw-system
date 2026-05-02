#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat <<'EOF'
Usage: ./scripts/setup-layer-env.sh [options]

Sets up the currently active Python environment for the layer1 and layer2 CLI
commands. This script assumes you have already created and sourced a virtualenv.

Options:
  --with-parsers      Install optional parser extras such as Camelot and PaddleOCR.
  --skip-db          Do not start Postgres or run Alembic migrations.
  --skip-install     Do not install/update the Python package.
  --db-url URL       DATABASE_URL to use for migrations. Defaults to .env or local Postgres.
  -h, --help         Show this help.

Examples:
  source .venv/bin/activate
  ./scripts/setup-layer-env.sh
  ./scripts/setup-layer-env.sh --with-parsers
  ./scripts/setup-layer-env.sh --db-url sqlite:///layer_demo.db
EOF
}

WITH_PARSERS=0
SKIP_DB=0
SKIP_INSTALL=0
DB_URL_OVERRIDE=""

while [[ $# -gt 0 ]]; do
  case "$1" in
    --with-parsers)
      WITH_PARSERS=1
      shift
      ;;
    --skip-db)
      SKIP_DB=1
      shift
      ;;
    --skip-install)
      SKIP_INSTALL=1
      shift
      ;;
    --db-url)
      DB_URL_OVERRIDE="${2:-}"
      if [[ -z "$DB_URL_OVERRIDE" ]]; then
        echo "error: --db-url requires a value" >&2
        exit 2
      fi
      shift 2
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      echo "error: unknown option: $1" >&2
      usage
      exit 2
      ;;
  esac
done

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$REPO_ROOT"

log() {
  printf '\n==> %s\n' "$1"
}

docker_compose() {
  if docker compose version >/dev/null 2>&1; then
    docker compose "$@"
  elif command -v docker-compose >/dev/null 2>&1; then
    docker-compose "$@"
  else
    echo "error: Docker Compose is required unless --skip-db or --db-url sqlite:///... is used." >&2
    exit 1
  fi
}

if [[ -z "${VIRTUAL_ENV:-}" ]]; then
  echo "error: no active virtualenv detected. Create and source one first, e.g.:" >&2
  echo "  python3 -m venv .venv && source .venv/bin/activate" >&2
  exit 1
fi

PYTHON_BIN="$(command -v python)"

log "Checking active Python environment"
"$PYTHON_BIN" - <<'PY'
import os
import sys

if sys.prefix == sys.base_prefix:
    raise SystemExit("Active python is not running inside a virtualenv")
if sys.version_info < (3, 11):
    raise SystemExit(f"Python 3.11+ is required; found {sys.version.split()[0]}")
print(f"Using {sys.executable}")
print(f"Virtualenv: {os.environ.get('VIRTUAL_ENV')}")
PY

if [[ ! -f .env ]]; then
  log "Creating .env from .env.example"
  cp .env.example .env
else
  log "Using existing .env"
fi

if [[ "$SKIP_INSTALL" -eq 0 ]]; then
  log "Installing Python package into active environment"
  python -m pip install --upgrade pip "setuptools<82" wheel
  if [[ "$WITH_PARSERS" -eq 1 ]]; then
    python -m pip install -e ".[dev,parsers]"
    python -m pip uninstall -y opencv-python opencv_python >/dev/null 2>&1 || true
    python -m pip install --force-reinstall opencv-python-headless
  else
    python -m pip install -e ".[dev]"
  fi
else
  log "Skipping package install"
fi

if [[ "$SKIP_DB" -eq 0 ]]; then
  if [[ -n "$DB_URL_OVERRIDE" ]]; then
    EFFECTIVE_DATABASE_URL="$DB_URL_OVERRIDE"
  else
    set -a
    # shellcheck disable=SC1091
    source .env
    set +a
    EFFECTIVE_DATABASE_URL="${DATABASE_URL:-postgresql+psycopg://layer1:layer1@localhost:5432/layer1}"
  fi

  if [[ "$EFFECTIVE_DATABASE_URL" != sqlite://* ]]; then
    if ! command -v docker >/dev/null 2>&1; then
      echo "error: Docker is required for non-SQLite setup unless --skip-db is used." >&2
      exit 1
    fi

    log "Starting Postgres"
    docker_compose up -d postgres

    log "Waiting for Postgres"
    for _ in $(seq 1 60); do
      if docker_compose exec -T postgres pg_isready -U layer1 -d layer1 >/dev/null 2>&1; then
        break
      fi
      sleep 1
    done
    docker_compose exec -T postgres pg_isready -U layer1 -d layer1 >/dev/null
  fi

  log "Running Alembic migrations"
  DATABASE_URL="$EFFECTIVE_DATABASE_URL" alembic upgrade head
else
  log "Skipping DB startup and migrations"
fi

log "Verifying CLI entry points"
layer1 --help >/dev/null
layer2 --help >/dev/null

log "Setup complete"
cat <<'EOF'
Ready to use:
  layer1 --help
  layer2 --help

Typical flow:
  layer1 ingest ./path/to/bylaw.pdf --municipality "Halifax" --bylaw-name "Regional Centre Land Use By-law" --enrich
  layer2 embed-fragments 1 --replace-existing
  layer2 answer 1 --question "What is permitted in HR-1?" --debug
EOF
