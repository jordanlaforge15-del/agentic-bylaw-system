from __future__ import annotations

import os
from logging.config import fileConfig

from alembic import context
from alembic.script import ScriptDirectory
from sqlalchemy import engine_from_config, pool

from layer1.db.base import Base
from layer1.db.migration_fence import fence_or_abort
import layer2.db.models  # noqa: F401
import layer2.compliance.db.models  # noqa: F401
import advisor.db.models  # noqa: F401

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def get_url() -> str:
    return os.getenv("DATABASE_URL", config.get_main_option("sqlalchemy.url"))


def run_migrations_offline() -> None:
    context.configure(
        url=get_url(),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def _alembic_command_name() -> str | None:
    """Which alembic subcommand is running, if the CLI told us.

    ``env.py`` is executed for read-only commands (``current``, ``history``)
    as well as for the ones that write, and we only want to fence the writers.
    Programmatic callers leave ``cmd_opts`` unset; those return ``None`` and
    are fenced defensively.
    """
    cmd = getattr(getattr(config, "cmd_opts", None), "cmd", None)
    if not cmd:
        return None
    return getattr(cmd[0], "__name__", None)


_FENCED_COMMANDS = {None, "upgrade", "downgrade"}


def _fence(connection) -> None:
    """Snapshot the dev DB before alembic writes to it (ABS-499).

    Prints ``ABORT: …`` and exits 3 — before the first DDL — if the snapshot
    cannot be taken. No-op for read-only subcommands, for any target that is
    not the dev database, and — on an upgrade — when the DB is already at head,
    so ``make migrate`` on an up-to-date checkout stays free.
    """
    command = _alembic_command_name()
    if command not in _FENCED_COMMANDS:
        return

    migration_context = context.get_context()
    current = tuple(sorted(migration_context.get_current_heads()))
    heads = tuple(sorted(ScriptDirectory.from_config(config).get_heads()))
    if command != "downgrade" and current == heads:
        return  # nothing pending — nothing to fence

    label = "-".join(current) if current else "unstamped"
    fence_or_abort(
        f"alembic-{command or 'migrate'}-from-{label}",
        database_url=connection.engine.url.render_as_string(hide_password=False),
        # Being behind is why this command is running; warning about it here
        # would be noise, and the drift check needs its own connection.
        check_drift=False,
    )


def run_migrations_online() -> None:
    configuration = config.get_section(config.config_ini_section, {})
    configuration["sqlalchemy.url"] = get_url()
    connectable = engine_from_config(configuration, prefix="sqlalchemy.", poolclass=pool.NullPool)
    with connectable.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata)
        _fence(connection)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
