"""ABS-501 — a stale ``DATABASE_URL`` must never outrank a live ``PG_PORT``.

Failure signature this pins (from the Data Model 3.0 post-mortem): a shell
carries ``DATABASE_URL`` exported for a stack that has since been torn down,
so pytest and Playwright's globalSetup both connect to a dead port and report
a fully green branch as six phantom Postgres failures.

    env -u DATABASE_URL pytest tests/test_feature_geometry_consistency_pg.py  -> 3 passed
    DATABASE_URL=...localhost:5443... pytest (same file)                      -> 3 failed

The rule: ``PG_PORT`` names the port of the stack that is up *now*, so on a
port disagreement it wins and the override is announced. Agreement is a no-op
— no false alarm when the two describe the same database.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from layer1.seed_guard import (
    apply_pg_port_precedence,
    default_e2e_database_url,
    reconcile_database_url,
)

REPO_ROOT = Path(__file__).resolve().parent.parent

STALE = "postgresql+psycopg://layer1:layer1@localhost:5443/layer1_test"
LIVE_PORT = "5435"
LIVE = "postgresql+psycopg://layer1:layer1@localhost:5435/layer1_test"


class TestReconcileDatabaseUrl:
    def test_conflict_resolves_to_pg_port(self) -> None:
        url, warning = reconcile_database_url({"DATABASE_URL": STALE, "PG_PORT": LIVE_PORT})
        assert url == LIVE
        assert warning is not None

    def test_conflict_warning_names_both_values(self) -> None:
        """The message has to be actionable: which port lost, which won."""
        _, warning = reconcile_database_url({"DATABASE_URL": STALE, "PG_PORT": LIVE_PORT})
        assert "5443" in warning
        assert f"PG_PORT={LIVE_PORT}" in warning
        assert "unset DATABASE_URL" in warning

    def test_conflict_preserves_credentials_and_database_name(self) -> None:
        url, _ = reconcile_database_url(
            {
                "DATABASE_URL": "postgresql+psycopg://someone:secret@db.local:5443/other_test?sslmode=disable",
                "PG_PORT": LIVE_PORT,
            }
        )
        assert url == (
            "postgresql+psycopg://someone:secret@db.local:5435/other_test?sslmode=disable"
        )

    def test_agreement_is_a_no_op(self) -> None:
        """No false alarm when both describe the same database."""
        url, warning = reconcile_database_url({"DATABASE_URL": LIVE, "PG_PORT": LIVE_PORT})
        assert url == LIVE
        assert warning is None

    def test_database_url_alone_is_untouched(self) -> None:
        """Without PG_PORT there is nothing to disagree with."""
        url, warning = reconcile_database_url({"DATABASE_URL": STALE})
        assert (url, warning) == (STALE, None)

    def test_unset_database_url_resolves_to_none(self) -> None:
        url, warning = reconcile_database_url({"PG_PORT": LIVE_PORT})
        assert (url, warning) == (None, None)

    def test_sqlite_url_is_exempt(self) -> None:
        url, warning = reconcile_database_url(
            {"DATABASE_URL": "sqlite:///tmp/x.db", "PG_PORT": LIVE_PORT}
        )
        assert (url, warning) == ("sqlite:///tmp/x.db", None)

    def test_portless_url_is_left_alone(self) -> None:
        """A URL with no port states no opinion about the port."""
        url, warning = reconcile_database_url(
            {"DATABASE_URL": "postgresql+psycopg://layer1@localhost/layer1_test", "PG_PORT": LIVE_PORT}
        )
        assert warning is None
        assert url == "postgresql+psycopg://layer1@localhost/layer1_test"

    def test_default_e2e_url_never_conflicts_with_its_own_pg_port(self) -> None:
        env = {"PG_PORT": LIVE_PORT}
        env["DATABASE_URL"] = default_e2e_database_url(env)
        assert reconcile_database_url(env)[1] is None


class TestApplyPgPortPrecedence:
    def test_mutates_env_and_warns_on_stderr(self, capsys: pytest.CaptureFixture[str]) -> None:
        env = {"DATABASE_URL": STALE, "PG_PORT": LIVE_PORT}
        assert apply_pg_port_precedence(env) == LIVE
        assert env["DATABASE_URL"] == LIVE
        assert "ABS-501" in capsys.readouterr().err

    def test_silent_when_they_agree(self, capsys: pytest.CaptureFixture[str]) -> None:
        env = {"DATABASE_URL": LIVE, "PG_PORT": LIVE_PORT}
        assert apply_pg_port_precedence(env) == LIVE
        assert capsys.readouterr().err == ""


def _settings_url(env_overrides: dict[str, str]) -> str:
    """Resolve ``get_settings().database_url`` in a clean subprocess.

    ``get_settings`` is ``lru_cache``d and the reconciliation runs inside
    it, so this has to be a fresh interpreter — not monkeypatched env in
    the current one.
    """
    import os

    env = {k: v for k, v in os.environ.items() if k not in {"DATABASE_URL", "PG_PORT"}}
    env.update(env_overrides)
    out = subprocess.run(
        [
            sys.executable,
            "-c",
            "from layer1.config import get_settings;"
            "import json;print(json.dumps(get_settings().database_url))",
        ],
        cwd=REPO_ROOT,
        env={**env, "PYTHONPATH": str(REPO_ROOT / "src")},
        capture_output=True,
        text=True,
        check=True,
    )
    return json.loads(out.stdout.strip().splitlines()[-1])


class TestPytestPath:
    """The pytest/FastAPI side: settings resolution, not just the helper."""

    def test_stale_database_url_is_redirected_to_pg_port(self) -> None:
        assert _settings_url({"DATABASE_URL": STALE, "PG_PORT": LIVE_PORT}) == LIVE

    def test_agreeing_database_url_survives_verbatim(self) -> None:
        assert _settings_url({"DATABASE_URL": LIVE, "PG_PORT": LIVE_PORT}) == LIVE


class TestSeedScriptPath:
    """The seed-script bootstrap (`scripts/e2e_db_default`) obeys the rule too."""

    def _resolved(self, env_overrides: dict[str, str]) -> str:
        import os

        env = {k: v for k, v in os.environ.items() if k not in {"DATABASE_URL", "PG_PORT"}}
        env.update(env_overrides)
        out = subprocess.run(
            [
                sys.executable,
                "-c",
                "import e2e_db_default;import os,json;"
                "print(json.dumps(os.environ['DATABASE_URL']))",
            ],
            cwd=REPO_ROOT,
            env={
                **env,
                "PYTHONPATH": f"{REPO_ROOT / 'src'}:{REPO_ROOT / 'scripts'}",
            },
            capture_output=True,
            text=True,
            check=True,
        )
        return json.loads(out.stdout.strip().splitlines()[-1])

    def test_stale_url_is_redirected(self) -> None:
        assert self._resolved({"DATABASE_URL": STALE, "PG_PORT": LIVE_PORT}) == LIVE

    def test_no_database_url_derives_from_pg_port(self) -> None:
        assert self._resolved({"PG_PORT": LIVE_PORT}) == LIVE
