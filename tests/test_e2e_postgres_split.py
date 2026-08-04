"""ABS-428 guards: dedicated ephemeral e2e Postgres, split from dev.

Three invariants, each of which quietly regressing would reopen the
e2e-fixture-contamination path:

1. docker-compose topology — two Postgres services: ``postgres`` (dev,
   :5432 default, ``layer1``, persistent volume) and ``postgres-e2e``
   (e2e, :5433 default via ``E2E_POSTGRES_HOST_PORT``, ``layer1_test``,
   its own volume, behind the ``e2e`` profile so dev-side compose
   commands cannot see it).
2. Script scoping — e2e scripts only ever address ``postgres-e2e``;
   ``dev-up.sh`` only ever addresses ``postgres``; ``e2e-down.sh``
   destroys the e2e container *and* volume.
3. Seed-script defaults — every ``scripts/seed_e2e_*.py`` imports
   ``e2e_db_default`` before any advisor/layer1 import, so a seed run
   with no explicit ``DATABASE_URL`` targets the e2e instance (and
   fails connection-refused when it is down) instead of writing into
   the dev database.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent
COMPOSE = REPO_ROOT / "docker-compose.yml"
SCRIPTS = REPO_ROOT / "scripts"


def _compose() -> dict:
    return yaml.safe_load(COMPOSE.read_text(encoding="utf-8"))


# ---------------------------------------------------------------------------
# 1. Compose topology
# ---------------------------------------------------------------------------


def test_compose_has_two_postgres_services() -> None:
    services = _compose()["services"]
    assert "postgres" in services, "dev postgres service missing"
    assert "postgres-e2e" in services, "dedicated e2e postgres service missing"


def test_dev_postgres_hosts_layer1_only_on_5432() -> None:
    svc = _compose()["services"]["postgres"]
    assert svc["environment"]["POSTGRES_DB"] == "layer1"
    assert svc["ports"] == ["${POSTGRES_HOST_PORT:-5432}:5432"]
    assert "layer1-postgres:/var/lib/postgresql/data" in svc["volumes"]
    assert "profiles" not in svc, "dev postgres must stay in the default profile"


def test_e2e_postgres_is_isolated_and_defaults_to_5433() -> None:
    svc = _compose()["services"]["postgres-e2e"]
    assert svc["environment"]["POSTGRES_DB"] == "layer1_test"
    assert svc["ports"] == ["${E2E_POSTGRES_HOST_PORT:-5433}:5432"], (
        "e2e instance must publish on its own host-port knob, default 5433 "
        "(PG_PORT=543X convention)"
    )
    assert svc["profiles"] == ["e2e"], (
        "postgres-e2e must sit behind the `e2e` profile so bare / dev-side "
        "compose commands (up, down, db-up, db-down, dev-up.sh) can neither "
        "start nor stop it"
    )
    assert "layer1-postgres-e2e:/var/lib/postgresql/data" in svc["volumes"]


def test_postgres_volumes_are_distinct() -> None:
    doc = _compose()
    dev_mounts = set(doc["services"]["postgres"]["volumes"])
    e2e_mounts = set(doc["services"]["postgres-e2e"]["volumes"])
    assert not dev_mounts & e2e_mounts, "dev and e2e must not share a data volume"
    assert "layer1-postgres-e2e" in doc["volumes"]


# ---------------------------------------------------------------------------
# 2. Script scoping
# ---------------------------------------------------------------------------


def test_e2e_up_targets_only_the_e2e_service() -> None:
    src = (SCRIPTS / "e2e-up.sh").read_text(encoding="utf-8")
    assert 'E2E_PG_SERVICE="postgres-e2e"' in src
    assert "E2E_POSTGRES_HOST_PORT" in src
    # The dev-side host-port knob must not be exported by the e2e path.
    assert not re.search(r"^export POSTGRES_HOST_PORT", src, re.MULTILINE), (
        "e2e-up.sh must not export POSTGRES_HOST_PORT — that knob belongs "
        "to the dev postgres service"
    )
    # No compose invocation may address the dev service. Every compose verb
    # in the script goes through docker_compose(); check none of them names
    # the bare `postgres` service.
    for verb in ("up -d", "exec -T", "rm -fs", "ps -aq"):
        assert not re.search(rf"{re.escape(verb)}\w*\s+postgres\b(?!-e2e)", src), (
            f"e2e-up.sh addresses the dev postgres service via `{verb} postgres`"
        )
    assert 'PG_PORT="${PG_PORT:-5433}"' in src, "e2e default port must be 5433"


def test_e2e_down_destroys_container_and_volume_but_not_dev() -> None:
    src = (SCRIPTS / "e2e-down.sh").read_text(encoding="utf-8")
    assert "rm -fsv postgres-e2e" in src, "e2e-down must destroy the e2e container"
    assert "layer1-postgres-e2e" in src, "e2e-down must remove the e2e volume"
    assert not re.search(r"(rm|stop|down|exec)[^\n]*\spostgres\b(?!-e2e)", src), (
        "e2e-down.sh must never stop/remove the dev postgres service"
    )
    assert "layer1-postgres:" not in src


def test_dev_up_never_touches_the_e2e_service() -> None:
    src = (SCRIPTS / "dev-up.sh").read_text(encoding="utf-8")
    assert "postgres-e2e" not in src.replace(
        "`postgres-e2e`", ""
    ), "dev-up.sh must not address the postgres-e2e service (comments excepted)"
    assert "docker_compose up -d postgres" in src
    assert "E2E_POSTGRES_HOST_PORT" not in src


# ---------------------------------------------------------------------------
# 3. Seed-script connection defaults
# ---------------------------------------------------------------------------


def _seed_scripts() -> list[Path]:
    return sorted(SCRIPTS.glob("seed_e2e_*.py"))


def test_seed_scripts_exist() -> None:
    assert len(_seed_scripts()) >= 1


@pytest.mark.parametrize(
    "path", _seed_scripts(), ids=lambda p: str(p.relative_to(REPO_ROOT))
)
def test_seed_script_imports_e2e_db_default_first(path: Path) -> None:
    """Every seed script must import e2e_db_default before any
    advisor/layer1 import, so its no-env default is the e2e instance
    rather than the dev database (layer1.config's default)."""
    source = path.read_text(encoding="utf-8")
    bootstrap_at = source.find("import e2e_db_default")
    assert bootstrap_at != -1, (
        f"{path.relative_to(REPO_ROOT)} does not import e2e_db_default — "
        f"run with default settings it would target the DEV database. "
        f"Add, before any advisor/layer1 import:\n\n"
        f"  import e2e_db_default  # noqa: F401  isort: skip\n"
    )
    project_imports = [
        source.find(marker)
        for marker in ("from advisor", "import advisor", "from layer1", "import layer1")
        if source.find(marker) != -1
    ]
    if project_imports:
        assert bootstrap_at < min(project_imports), (
            f"{path.relative_to(REPO_ROOT)}: e2e_db_default must be imported "
            f"BEFORE advisor/layer1 modules (get_settings() is lru_cached; a "
            f"later import is too late to influence DATABASE_URL resolution)"
        )


def test_e2e_db_default_targets_e2e_port_and_db(monkeypatch: pytest.MonkeyPatch) -> None:
    import importlib
    import sys

    monkeypatch.syspath_prepend(str(SCRIPTS))
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.delenv("PG_PORT", raising=False)
    sys.modules.pop("e2e_db_default", None)
    mod = importlib.import_module("e2e_db_default")
    assert (
        mod.default_e2e_database_url()
        == "postgresql+psycopg://layer1:layer1@localhost:5433/layer1_test"
    )
    monkeypatch.setenv("PG_PORT", "5437")
    assert (
        mod.default_e2e_database_url()
        == "postgresql+psycopg://layer1:layer1@localhost:5437/layer1_test"
    )
    sys.modules.pop("e2e_db_default", None)
