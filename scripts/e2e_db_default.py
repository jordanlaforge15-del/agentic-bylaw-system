"""Default e2e seed scripts to the dedicated e2e Postgres instance (ABS-428).

Imported for its side effect as the FIRST project import of every
``scripts/seed_e2e_*.py``. It must run before anything imports
``layer1.config`` (whose ``get_settings`` is ``lru_cache``d), so the
resolved settings pick up the e2e default.

Why: ``layer1.config.Settings.database_url`` defaults to the DEV
database (``localhost:5432/layer1``). Before the e2e/dev Postgres split,
a seed script run without an explicit ``DATABASE_URL`` would therefore
happily write synthetic e2e fixtures into the dev corpus — the exact
contamination ABS-428/ABS-429 exist to eliminate. With this module in
place, the no-env default is the dedicated ephemeral e2e instance
(compose service ``postgres-e2e``, host port ``PG_PORT`` or 5433), so a
default-configured seed run while the e2e stack is down fails with
connection-refused instead of touching dev data.

Resolution order:
1. An explicit ``DATABASE_URL`` in the environment always wins
   (``os.environ.setdefault`` — this is how scripts/e2e-up.sh and the
   Playwright specs drive the seeds).
2. Otherwise: ``postgresql+psycopg://layer1:layer1@localhost:{PG_PORT
   or 5433}/layer1_test`` — honoring the per-worktree ``PG_PORT=543X``
   convention.

Note: because this sets a process ENVIRONMENT variable, it also
outranks any ``DATABASE_URL`` in a repo-root ``.env`` file (pydantic
precedence: env var > dotenv). That is deliberate — a dev-pointing
``.env`` must never redirect an e2e seed script at the dev database.
"""
from __future__ import annotations

import os

E2E_PG_PORT_DEFAULT = "5433"


def default_e2e_database_url() -> str:
    port = os.environ.get("PG_PORT", E2E_PG_PORT_DEFAULT)
    return f"postgresql+psycopg://layer1:layer1@localhost:{port}/layer1_test"


os.environ.setdefault("DATABASE_URL", default_e2e_database_url())
