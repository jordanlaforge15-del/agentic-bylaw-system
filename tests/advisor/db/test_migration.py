"""Verify the advisor models are wired into the shared ``Base.metadata``.

Running every Alembic migration head-to-tip on sqlite is more
ceremony than this test needs — what we actually want to know is that
``Base.metadata.create_all(...)`` (which is what tests use everywhere)
materialises the four ``advisor_*`` tables with the columns the spec
calls for. Importing ``advisor.db.models`` is enough to register them
on ``Base``; this is the same mechanism alembic/env.py uses.
"""
from __future__ import annotations

from pathlib import Path

from sqlalchemy import inspect

import advisor.db.models  # noqa: F401  ensure models are bound to Base
from layer1.db.base import Base
from layer1.db.init_db import create_all
from layer1.db.session import make_engine


EXPECTED_COLUMNS = {
    "advisor_user": {
        "id",
        "clerk_user_id",
        "email",
        "full_name",
        "created_at",
        "updated_at",
        "plan_tier",
        "monthly_query_limit",
        "monthly_queries_used",
        "month_started_at",
        "stripe_customer_id",
        "stripe_subscription_id",
        "subscription_status",
        "subscription_current_period_end",
        "metadata_json",
    },
    "advisor_chat_session": {
        "id",
        "user_id",
        "title",
        "created_at",
        "updated_at",
        "metadata_json",
    },
    "advisor_chat_message": {
        "id",
        "session_id",
        "sequence",
        "role",
        "content_json",
        "tool_calls_json",
        "tokens_input",
        "tokens_output",
        "created_at",
    },
    "advisor_usage_event": {
        "id",
        "user_id",
        "session_id",
        "event_type",
        "provider",
        "model",
        "tokens_input",
        "tokens_output",
        "cost_estimate_cents",
        "metadata_json",
        "created_at",
    },
}


def test_advisor_tables_present_on_base_metadata() -> None:
    table_names = set(Base.metadata.tables.keys())
    for name in EXPECTED_COLUMNS:
        assert name in table_names, f"{name} missing from Base.metadata"


def test_advisor_tables_have_expected_columns() -> None:
    for table_name, expected_cols in EXPECTED_COLUMNS.items():
        table = Base.metadata.tables[table_name]
        actual_cols = {c.name for c in table.columns}
        missing = expected_cols - actual_cols
        assert not missing, f"{table_name} missing columns: {missing}"


def test_advisor_tables_materialise_in_sqlite(tmp_path: Path) -> None:
    db_url = f"sqlite:///{tmp_path / 'advisor.db'}"
    create_all(db_url)

    inspector = inspect(make_engine(db_url))
    sqlite_tables = set(inspector.get_table_names())
    for name in EXPECTED_COLUMNS:
        assert name in sqlite_tables, f"{name} not created in sqlite"


def test_chat_message_session_sequence_unique_constraint_present() -> None:
    table = Base.metadata.tables["advisor_chat_message"]
    constraint_names = {c.name for c in table.constraints}
    assert "uq_advisor_chat_message_session_sequence" in constraint_names


def test_user_id_columns_have_foreign_keys() -> None:
    chat_session = Base.metadata.tables["advisor_chat_session"]
    fk = next(iter(chat_session.c.user_id.foreign_keys))
    assert fk.column.table.name == "advisor_user"
    assert fk.ondelete == "CASCADE"

    usage = Base.metadata.tables["advisor_usage_event"]
    user_fk = next(iter(usage.c.user_id.foreign_keys))
    assert user_fk.ondelete == "CASCADE"
    session_fk = next(iter(usage.c.session_id.foreign_keys))
    assert session_fk.ondelete == "SET NULL"
