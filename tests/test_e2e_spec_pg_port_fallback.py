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


def _collect_ts_files() -> list[Path]:
    return sorted(E2E_ROOT.rglob("*.ts"))


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
        f"  const pgPort = process.env.PG_PORT || \"5432\";\n"
        f"  const databaseUrl =\n"
        f"    process.env.DATABASE_URL ||\n"
        f"    `postgresql+psycopg://layer1:layer1@localhost:${{pgPort}}/layer1_test`;\n\n"
        f"Without PG_PORT awareness, the seed lands in the default "
        f":5432 instance while the worktree's FastAPI queries a "
        f"different DB. See ABS-207."
    )
