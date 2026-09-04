#!/usr/bin/env bash
#
# ABS-532 — regenerate the committed Python dependency locks.
#
# WHY THIS EXISTS. `pyproject.toml` declares floors (`fastapi>=0.115`), not
# versions. Before this script there were five independent `pip install` sites
# (Dockerfile.advisor, dev-setup.sh, two CI jobs, dependency-audit.yml), each
# resolving those floors on its own clock, and nothing recorded or compared what
# any of them landed on. That is how anthropic 1.x reached prod while the test
# suite ran happily against the 0.100.0 the dev venv had installed months
# earlier (ABS-531). The lock files this script writes are the single answer all
# five sites now install from.
#
# WHY uv, AND WHY --universal. The deploy target is linux / CPython 3.11
# (Dockerfile.advisor, CI's python-version). The dev venv is macOS / CPython
# 3.12. A hash-pinned lock compiled by `pip-compile` against one of those does
# not reliably install on the other — the resolution is baked to the compiling
# interpreter and platform, so the macOS dev path would need a second,
# separately-resolved file and the two would drift apart, which is the bug this
# ticket is about, one level down.
#
# `uv pip compile --universal` resolves for *all* platforms and all Python
# versions >= the project's requires-python in one pass, forking a requirement
# into marker-guarded lines when it genuinely must differ (see the two `numpy`
# entries in the emitted files: one for < 3.12, one for >= 3.12). Every wheel
# hash for every supported platform is emitted, so one committed file installs
# byte-identically on linux/3.11 and macOS/3.12.
#
# uv is used ONLY here, to compile. Nothing installs with it: the five sites all
# run `pip install --require-hashes -r requirements/<lock>.txt`, so no install
# site grows a new tool dependency and the locks remain plain pip-readable.
#
# USAGE
#   ./scripts/lock-python-deps.sh            # regenerate the locks in place
#   ./scripts/lock-python-deps.sh --check    # fail if the committed locks are stale
#   ./scripts/lock-python-deps.sh --upgrade  # deliberately move versions forward
#
# Without --upgrade, uv holds every already-locked version it still can, so
# re-running after a pyproject edit moves only what the edit forces. Upgrading
# is an explicit act that shows up as a reviewable diff — see
# docs/PYTHON_DEPENDENCY_LOCKS.md.
set -euo pipefail

# Pinned deliberately: an unpinned locker is itself an unlocked dependency, and
# a resolver that changes under us reintroduces exactly the drift the locks
# exist to remove. Bump this the same way you bump anything else — on purpose,
# in a diff.
UV_VERSION="0.9.28"

# Locked against the deploy target's interpreter, not the dev box's. Universal
# resolution still covers 3.12+, but the floor is what the image runs.
LOCK_PYTHON_VERSION="3.11"

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

REQUIREMENTS_DIR="$REPO_ROOT/requirements"

# Each lock and the extras it resolves. Keep in sync with the table in
# docs/PYTHON_DEPENDENCY_LOCKS.md and with tests/test_dependency_locks.py,
# which asserts every install site points at one of these.
#
#   base.txt    — no extras. The golden-gate CI job installs this, and the
#                 gate's imports are required to stay inside it (ci.yml has the
#                 long-form reasoning: the gate once died importing FastAPI and
#                 reported a hold it had never evaluated).
#   runtime.txt — [advisor]. What Dockerfile.advisor ships. No pytest, no ruff.
#   dev.txt     — [dev,advisor]. The dev venv, the pytest CI job, and the
#                 vulnerability audit.
LOCK_SPECS=(
  "base.txt:"
  "runtime.txt:advisor"
  "dev.txt:dev,advisor"
)

MODE="write"
UPGRADE=0
for arg in "$@"; do
  case "$arg" in
    --check) MODE="check" ;;
    --upgrade) UPGRADE=1 ;;
    -h|--help) sed -n '2,40p' "${BASH_SOURCE[0]}"; exit 0 ;;
    *) echo "error: unknown argument '$arg'" >&2; exit 2 ;;
  esac
done

if [[ "$MODE" == "check" && "$UPGRADE" -eq 1 ]]; then
  echo "error: --check and --upgrade are contradictory" >&2
  exit 2
fi

log() { printf '\n\033[1;34m==>\033[0m %s\n' "$1"; }

# ---------------------------------------------------------------------------
# Get the pinned uv, without requiring it to be installed globally.
#
# A cached venv under .uv-lock-venv/ (gitignored) so repeat runs are instant.
# It is rebuilt whenever UV_VERSION moves, which is the only time the resolver
# is allowed to change.
# ---------------------------------------------------------------------------
UV_VENV="$REPO_ROOT/.uv-lock-venv"
UV_BIN="$UV_VENV/bin/uv"

resolve_uv() {
  if [[ -x "$UV_BIN" ]] && "$UV_BIN" --version 2>/dev/null | grep -q "uv $UV_VERSION"; then
    return
  fi
  if command -v uv >/dev/null 2>&1 && uv --version 2>/dev/null | grep -q "uv $UV_VERSION"; then
    UV_BIN="$(command -v uv)"
    return
  fi
  log "Installing uv $UV_VERSION into .uv-lock-venv"
  rm -rf "$UV_VENV"
  # Any interpreter can host uv; it ships as a self-contained binary and does
  # not resolve against the host Python. Prefer python3 on PATH.
  python3 -m venv "$UV_VENV"
  "$UV_VENV/bin/python" -m pip install --quiet --upgrade pip
  "$UV_VENV/bin/python" -m pip install --quiet "uv==$UV_VERSION"
  UV_BIN="$UV_VENV/bin/uv"
}

resolve_uv
log "Using $("$UV_BIN" --version)"

# uv (like pip-compile) reads the file already at -o and holds every version it
# names that is still satisfiable. That is what makes a regeneration diff
# reviewable: after a pyproject edit only what the edit forces moves. It is also
# why --check copies the committed lock into the scratch path before compiling —
# compiling to a bare temp file would re-resolve from scratch and report drift
# every time anything on PyPI released, which is noise, not signal.
compile_one() {
  local out_path="$1" extras="$2"
  local args=(
    pip compile
    --universal
    --generate-hashes
    --python-version "$LOCK_PYTHON_VERSION"
    # Extras are stripped (`pyjwt`, not `pyjwt[crypto]`) on purpose. Nothing is
    # lost — an extra's own requirements are already pinned as their own lines
    # — and it keeps the emitted file usable as a pip *constraints* file, which
    # pip rejects outright when entries carry extras. dev-setup.sh relies on
    # that for the unlocked [parsers] path.
    # The header records the regeneration command rather than the literal
    # invocation, so a --check run compiling to a temp path produces a
    # byte-identical file and drift means drift.
    --custom-compile-command "./scripts/lock-python-deps.sh"
    --quiet
    -o "$out_path"
  )
  if [[ -n "$extras" ]]; then
    local extra
    # `--extra dev,advisor` is one token to uv; split it into repeated flags.
    for extra in ${extras//,/ }; do
      args+=(--extra "$extra")
    done
  fi
  if [[ "$UPGRADE" -eq 1 ]]; then
    args+=(--upgrade)
  fi
  "$UV_BIN" "${args[@]}" pyproject.toml
}

mkdir -p "$REQUIREMENTS_DIR"

if [[ "$MODE" == "check" ]]; then
  SCRATCH="$(mktemp -d)"
  trap 'rm -rf "$SCRATCH"' EXIT
fi

STALE=()
for spec in "${LOCK_SPECS[@]}"; do
  name="${spec%%:*}"
  extras="${spec#*:}"
  committed="$REQUIREMENTS_DIR/$name"

  if [[ "$MODE" == "check" ]]; then
    target="$SCRATCH/$name"
    # Seed the scratch file with the committed lock so uv holds its pins; see
    # the note on compile_one.
    [[ -f "$committed" ]] && cp "$committed" "$target"
  else
    target="$committed"
  fi

  log "Compiling $name (extras: ${extras:-none})"
  compile_one "$target" "$extras"

  if [[ "$MODE" == "check" ]]; then
    if [[ ! -f "$committed" ]]; then
      STALE+=("$name (missing)")
    elif ! diff -q "$committed" "$target" >/dev/null; then
      STALE+=("$name")
      echo "--- committed requirements/$name"
      echo "+++ freshly compiled"
      diff -u "$committed" "$target" || true
    fi
  fi
done

if [[ "$MODE" == "check" ]]; then
  if [[ ${#STALE[@]} -gt 0 ]]; then
    cat >&2 <<EOF

Lock drift detected in: ${STALE[*]}

The committed lock files do not match what ./scripts/lock-python-deps.sh
produces from the current pyproject.toml. Regenerate and commit the result:

    ./scripts/lock-python-deps.sh

If you meant to move dependency versions forward, that is --upgrade, and the
version bumps belong in their own reviewed commit. See
docs/PYTHON_DEPENDENCY_LOCKS.md.
EOF
    exit 1
  fi
  log "Locks are in sync with pyproject.toml"
  exit 0
fi

log "Wrote:"
for spec in "${LOCK_SPECS[@]}"; do
  echo "  requirements/${spec%%:*}"
done
cat <<'EOF'

Next: commit the regenerated locks. Every install site reads them:

  Dockerfile.advisor                    requirements/runtime.txt
  scripts/dev-setup.sh                  requirements/dev.txt
  .github/workflows/ci.yml (tests)      requirements/dev.txt
  .github/workflows/ci.yml (gate)       requirements/base.txt
  .github/workflows/dependency-audit.yml requirements/dev.txt
EOF
