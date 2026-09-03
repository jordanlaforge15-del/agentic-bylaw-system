"""ABS-207: lint test — every Playwright spec / globalSetup that resolves
``DATABASE_URL`` must derive its fallback from ``PG_PORT``, not hard-code
port 5432.

CLAUDE.md docs promise ``make e2e`` from a worktree with ``PG_PORT=543X``
works without extra exports. The shell-level ``DATABASE_URL`` exported
inside ``scripts/e2e-up.sh`` does not survive the make recipe's per-line
subshell, so the TypeScript fallback path is what actually runs. Without
PG_PORT awareness there, the seeds land in the default ``:5432``
instance while FastAPI on the worktree's overridden port queries a
different DB — 27 evaluator/retrieval specs returned ``uncertain`` /
404 silently before this lint was in place.

This test scans for the fallback string and asserts each occurrence is
in a file that also references ``PG_PORT``.
"""

from __future__ import annotations

from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parent.parent
E2E_ROOT = REPO_ROOT / "web" / "e2e"
HARDCODED_FALLBACK = (
    '"postgresql+psycopg://layer1:layer1@localhost:5432/layer1_test"'
)


HELPER = E2E_ROOT / "helpers" / "database-url.ts"


def _collect_ts_files() -> list[Path]:
    # The helper *is* the resolver; it necessarily contains the patterns
    # the lints below forbid everywhere else.
    return sorted(p for p in E2E_ROOT.rglob("*.ts") if p != HELPER)


@pytest.mark.parametrize("path", _collect_ts_files(), ids=lambda p: str(p.relative_to(REPO_ROOT)))
def test_no_hardcoded_5432_fallback(path: Path) -> None:
    """Reject any file that hard-codes the :5432 fallback URL.

    The PG_PORT-aware form uses a template literal
    (``postgresql+psycopg://layer1:layer1@localhost:${pgPort}/...``)
    so the literal-string check is enough to catch a regression — a
    PG_PORT-aware spec would never contain the bare ``:5432`` literal.
    """
    source = path.read_text(encoding="utf-8")
    assert HARDCODED_FALLBACK not in source, (
        f"{path.relative_to(REPO_ROOT)} hard-codes the :5432 DATABASE_URL "
        f"fallback. Honor PG_PORT instead:\n\n"
        f"  const pgPort = process.env.PG_PORT || \"5433\";\n"
        f"  const databaseUrl =\n"
        f"    process.env.DATABASE_URL ||\n"
        f"    `postgresql+psycopg://layer1:layer1@localhost:${{pgPort}}/layer1_test`;\n\n"
        f"Without PG_PORT awareness, the seed lands in the default "
        f"instance while the worktree's FastAPI queries a "
        f"different DB. See ABS-207."
    )


@pytest.mark.parametrize("path", _collect_ts_files(), ids=lambda p: str(p.relative_to(REPO_ROOT)))
def test_pg_port_fallback_targets_e2e_instance(path: Path) -> None:
    """ABS-428: the PG_PORT *fallback* must be 5433 (dedicated e2e
    instance), never 5432 (dev instance).

    After the e2e/dev Postgres split, port 5432 is the dev instance and
    hosts ``layer1`` only. A spec that falls back to ``PG_PORT || "5432"``
    would aim its seeds at the dev instance whenever the caller relies on
    defaults — recreating the fixture-contamination path the split
    removed.
    """
    source = path.read_text(encoding="utf-8")
    assert 'process.env.PG_PORT || "5432"' not in source, (
        f"{path.relative_to(REPO_ROOT)} falls back to the DEV Postgres "
        f'port. Use `process.env.PG_PORT || "5433"` — the dedicated e2e '
        f"instance default. See ABS-428."
    )


@pytest.mark.parametrize("path", _collect_ts_files(), ids=lambda p: str(p.relative_to(REPO_ROOT)))
def test_database_url_resolved_through_the_helper(path: Path) -> None:
    """ABS-501: no spec may read ``process.env.DATABASE_URL`` itself.

    The inlined form (``process.env.DATABASE_URL || <PG_PORT fallback>``)
    prefers an *inherited* DATABASE_URL, which routinely outlives the
    stack it described — e2e-up.sh and the Night Manager's agent runner
    both export one. A stale value then aims the seed at a dead port
    while FastAPI queries the live one, and the suite reports a green
    branch as broken.

    ``web/e2e/helpers/database-url.ts`` owns the precedence rule (PG_PORT
    wins on disagreement, loudly). Everyone else calls it.
    """
    source = path.read_text(encoding="utf-8")
    assert "process.env.DATABASE_URL" not in source, (
        f"{path.relative_to(REPO_ROOT)} resolves DATABASE_URL inline. Use the "
        f"shared helper instead:\n\n"
        f'  import {{ resolveDatabaseUrl }} from "../helpers/database-url";\n'
        f"  const databaseUrl = resolveDatabaseUrl();\n\n"
        f"Inline resolution prefers a stale inherited DATABASE_URL over the "
        f"live stack's PG_PORT. See ABS-501."
    )
