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

ABS-430 hard refusal: after the default is applied, this module runs
``layer1.seed_guard.require_test_database()`` at import time. Whatever
the effective ``DATABASE_URL`` resolves to, the seed process aborts
(exit 1, clear message, nothing written) unless the target database's
name ends in ``_test`` — or ``E2E_SEED_ALLOW_DB=<exact-db-name>``
explicitly whitelists it. The default above makes the common no-env case
safe; the guard makes the explicit-URL case safe too.

Importing ``layer1.seed_guard`` here is fine w.r.t. the "before any
layer1 import" contract: the guard module is pure stdlib and pulls in
nothing but ``layer1/__init__`` (a docstring) — in particular it never
touches ``layer1.config``, so ``get_settings``'s lru_cache still
resolves after the env default is in place.
"""
from __future__ import annotations

import os

from layer1.seed_guard import (  # noqa: F401  (re-exported for callers/tests)
    E2E_PG_PORT_DEFAULT,
    default_e2e_database_url,
    require_test_database,
)

os.environ.setdefault("DATABASE_URL", default_e2e_database_url())

# ABS-430: refuse to run against any non-test database. SystemExit here
# kills the importing seed script before it opens a single connection.
require_test_database()
