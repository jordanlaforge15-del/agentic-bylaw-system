#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat <<'EOF'
Usage: ./scripts/dev-setup.sh [options]

Creates a local Python virtual environment, installs the project, starts the
Postgres/pgvector container, waits for it, and runs Alembic migrations.

Options:
  --with-parsers      Install heavy parser extras such as Docling, Camelot, and PaddleOCR.
  --skip-db          Do not start Postgres or run Alembic.
  --python PATH      Python executable to use for the virtual environment.
  -h, --help         Show this help.

Examples:
  ./scripts/dev-setup.sh
  ./scripts/dev-setup.sh --with-parsers
  ./scripts/dev-setup.sh --python python3.12
EOF
}

WITH_PARSERS=0
SKIP_DB=0
PYTHON_BIN=""

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
    --python)
      PYTHON_BIN="${2:-}"
      if [[ -z "$PYTHON_BIN" ]]; then
        echo "error: --python requires a value" >&2
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

find_python() {
  if [[ -n "$PYTHON_BIN" ]]; then
    command -v "$PYTHON_BIN"
    return
  fi

  for candidate in python3.12 python3.11 python3; do
    if command -v "$candidate" >/dev/null 2>&1; then
      command -v "$candidate"
      return
    fi
  done

  echo "error: no Python 3.11+ executable found. Install Python 3.12 or pass --python PATH." >&2
  exit 1
}

docker_compose() {
  if docker compose version >/dev/null 2>&1; then
    docker compose "$@"
  elif command -v docker-compose >/dev/null 2>&1; then
    docker-compose "$@"
  else
    echo "error: Docker Compose is required. Install Docker Desktop or docker compose." >&2
    exit 1
  fi
}

PYTHON_PATH="$(find_python)"

log "Checking Python"
"$PYTHON_PATH" - <<'PY'
import sys

if sys.version_info < (3, 11):
    raise SystemExit(f"Python 3.11+ is required; found {sys.version.split()[0]}")
print(f"Using Python {sys.version.split()[0]}")
if sys.version_info >= (3, 14):
    print("warning: Python 3.14+ may have dependency friction; Python 3.12 is preferred.")
PY

if [[ ! -f .env ]]; then
  log "Creating .env from .env.example"
  cp .env.example .env
else
  log "Using existing .env"
fi

log "Creating virtual environment"
if [[ -d .venv && ! -x .venv/bin/python ]]; then
  echo "Existing .venv is not usable in this shell; recreating it."
  rm -rf .venv
fi
if [[ ! -d .venv ]]; then
  "$PYTHON_PATH" -m venv .venv
fi

VENV_PYTHON="$REPO_ROOT/.venv/bin/python"
if [[ ! -x "$VENV_PYTHON" ]]; then
  echo "error: .venv exists but .venv/bin/python is missing or not executable" >&2
  exit 1
fi

log "Installing Python package"
"$VENV_PYTHON" -m pip install --upgrade pip setuptools wheel
if [[ "$WITH_PARSERS" -eq 1 ]]; then
  "$VENV_PYTHON" -m pip install -e ".[dev,parsers]"
  "$VENV_PYTHON" -m pip uninstall -y opencv-python opencv_python >/dev/null 2>&1 || true
  "$VENV_PYTHON" -m pip install --force-reinstall opencv-python-headless
else
  "$VENV_PYTHON" -m pip install -e ".[dev]"
fi

if [[ "$SKIP_DB" -eq 0 ]]; then
  if ! command -v docker >/dev/null 2>&1; then
    echo "error: Docker is required unless --skip-db is used." >&2
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

  log "Running Alembic migrations"
  set -a
  # shellcheck disable=SC1091
  source .env
  set +a
  EFFECTIVE_DATABASE_URL="${DATABASE_URL:-postgresql+psycopg://layer1:layer1@localhost:5432/layer1}"
  if [[ -f /.dockerenv && "$EFFECTIVE_DATABASE_URL" == *"@localhost:"* ]]; then
    EFFECTIVE_DATABASE_URL="${EFFECTIVE_DATABASE_URL/@localhost:/@postgres:}"
    echo "Detected container shell; using Docker Compose hostname in DATABASE_URL."
  fi
  DATABASE_URL="$EFFECTIVE_DATABASE_URL" "$REPO_ROOT/.venv/bin/alembic" upgrade head
fi

log "Setup complete"
cat <<EOF
Next commands:
  source .venv/bin/activate
  layer1 ingest "regionalcentrelub-eff-26april13-case24469toclinked.pdf" --municipality "Halifax" --bylaw-name "Regional Centre Land Use By-law"
  layer2 embed-fragments 1 --replace-existing
  layer2 answer 1 --question "What is the minimum front setback in COR?" --debug
EOF
