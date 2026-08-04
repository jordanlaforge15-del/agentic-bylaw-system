"""ABS-430: seed paths hard-refuse non-test databases.

Covers the shared preflight (``layer1.seed_guard.require_test_database``)
on its three contract paths — allowed ``*_test`` name, refused non-test
name, ``E2E_SEED_ALLOW_DB`` override — plus the two wiring points that
give the guard its teeth:

* ``scripts/e2e_db_default`` (imported first by every
  ``scripts/seed_e2e_*.py``) runs the guard at import time, so a seed
  process pointed at e.g. ``layer1`` dies before opening a connection.
* ``advisor.api.e2e_server`` runs the guard before any advisor/layer1
  import, so its ``/v1/_test/*`` seed endpoints can never be mounted
  over a non-test database.
"""

from __future__ import annotations

import importlib
import re
import sys
from pathlib import Path

import pytest

from layer1.seed_guard import (
    ALLOW_ENV_VAR,
    SeedTargetRefusedError,
    default_e2e_database_url,
    require_test_database,
    resolve_database_name,
)

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPTS = REPO_ROOT / "scripts"

DEV_URL = "postgresql+psycopg://layer1:layer1@localhost:5432/layer1"
TEST_URL = "postgresql+psycopg://layer1:layer1@localhost:5433/layer1_test"


# ---------------------------------------------------------------------------
# resolve_database_name
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        (DEV_URL, "layer1"),
        (TEST_URL, "layer1_test"),
        ("postgresql://u:p@db.example.com/layer1?sslmode=require", "layer1"),
        ("postgresql+psycopg://localhost/", ""),
        ("postgresql+psycopg://localhost", ""),
    ],
)
def test_resolve_database_name(url: str, expected: str) -> None:
    assert resolve_database_name(url) == expected


# ---------------------------------------------------------------------------
# require_test_database — the three contract paths
# ---------------------------------------------------------------------------


def test_allowed_path_test_suffix_passes() -> None:
    assert require_test_database(TEST_URL, env={}) == "layer1_test"
    assert require_test_database("postgresql://h/other_test", env={}) == "other_test"


def test_refused_path_non_test_name_aborts_with_clear_message() -> None:
    with pytest.raises(SeedTargetRefusedError) as excinfo:
        require_test_database(DEV_URL, env={})
    message = str(excinfo.value)
    assert "'layer1'" in message
    assert "_test" in message
    assert ALLOW_ENV_VAR in message
    # SystemExit with a string payload → interpreter exit status 1.
    assert not isinstance(excinfo.value.code, int)
    assert excinfo.value.code  # truthy → non-zero exit


def test_refusal_is_a_systemexit_so_unhandled_scripts_die() -> None:
    with pytest.raises(SystemExit):
        require_test_database(DEV_URL, env={})


def test_override_path_exact_name_proceeds(capsys: pytest.CaptureFixture) -> None:
    name = require_test_database(DEV_URL, env={ALLOW_ENV_VAR: "layer1"})
    assert name == "layer1"
    assert "whitelisted" in capsys.readouterr().err


# ---------------------------------------------------------------------------
# require_test_database — edges around the override and resolution
# ---------------------------------------------------------------------------


def test_override_must_match_exact_name() -> None:
    with pytest.raises(SeedTargetRefusedError):
        require_test_database(DEV_URL, env={ALLOW_ENV_VAR: "layer1_prod"})
    with pytest.raises(SeedTargetRefusedError):
        require_test_database(DEV_URL, env={ALLOW_ENV_VAR: ""})


def test_missing_or_unparseable_database_name_is_refused() -> None:
    with pytest.raises(SeedTargetRefusedError):
        require_test_database("postgresql+psycopg://localhost:5433/", env={})
    with pytest.raises(SeedTargetRefusedError):
        require_test_database(None, env={})  # no DATABASE_URL in env at all


def test_url_defaults_to_env_database_url() -> None:
    assert require_test_database(env={"DATABASE_URL": TEST_URL}) == "layer1_test"
    with pytest.raises(SeedTargetRefusedError):
        require_test_database(env={"DATABASE_URL": DEV_URL})


def test_sqlite_urls_are_exempt() -> None:
    """Unit-test harnesses boot the e2e app on tmp_path sqlite files;
    those are throwaway local files, not the Postgres corpus the guard
    protects, so they pass without the _test suffix."""
    require_test_database("sqlite:////tmp/e2e_evaluator.db", env={})
    require_test_database("sqlite+pysqlite:///:memory:", env={})


def test_default_e2e_url_is_always_allowed() -> None:
    for env in ({}, {"PG_PORT": "5439"}):
        assert require_test_database(default_e2e_database_url(env), env=env) == (
            "layer1_test"
        )


# ---------------------------------------------------------------------------
# Wiring point 1: scripts/e2e_db_default enforces the guard at import
# ---------------------------------------------------------------------------


def _import_bootstrap(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.syspath_prepend(str(SCRIPTS))
    sys.modules.pop("e2e_db_default", None)
    try:
        return importlib.import_module("e2e_db_default")
    finally:
        sys.modules.pop("e2e_db_default", None)


def test_bootstrap_refuses_dev_database_url(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DATABASE_URL", DEV_URL)
    monkeypatch.delenv(ALLOW_ENV_VAR, raising=False)
    with pytest.raises(SystemExit) as excinfo:
        _import_bootstrap(monkeypatch)
    assert "layer1" in str(excinfo.value)


def test_bootstrap_honors_explicit_whitelist(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DATABASE_URL", DEV_URL)
    monkeypatch.setenv(ALLOW_ENV_VAR, "layer1")
    mod = _import_bootstrap(monkeypatch)
    assert mod.default_e2e_database_url().endswith("/layer1_test")


def test_bootstrap_default_env_passes_guard(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.delenv(ALLOW_ENV_VAR, raising=False)
    monkeypatch.delenv("PG_PORT", raising=False)
    _import_bootstrap(monkeypatch)  # must not raise


# ---------------------------------------------------------------------------
# Wiring point 2: the e2e server preflights before advisor/layer1 imports
# ---------------------------------------------------------------------------


def test_e2e_server_guards_before_project_imports() -> None:
    source = (REPO_ROOT / "src/advisor/api/e2e_server.py").read_text(encoding="utf-8")
    guard_at = source.find("require_test_database()")
    assert guard_at != -1, "e2e_server must call require_test_database()"
    default_at = source.find('os.environ.setdefault("DATABASE_URL"')
    assert -1 < default_at < guard_at, (
        "e2e_server must default DATABASE_URL to the e2e instance before "
        "guarding, mirroring scripts/e2e_db_default"
    )
    first_project_import = min(
        m.start()
        for m in re.finditer(
            r"^from (?:advisor|layer1\.(?!seed_guard))", source, re.MULTILINE
        )
    )
    assert guard_at < first_project_import, (
        "the guard must run before advisor/layer1 imports so lru_cached "
        "settings can only resolve the guarded DATABASE_URL"
    )
