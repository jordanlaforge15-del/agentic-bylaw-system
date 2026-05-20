from __future__ import annotations

from layer1.db.base import Base
from layer1.db.session import make_engine


def create_all(db_url: str | None = None) -> None:
    # Side-import: the compliance ORM models live in ``layer2.compliance.db``
    # and aren't loaded by anything in layer 1 itself. Without this
    # import, ``Base.metadata`` doesn't know about ``submission``,
    # ``submission_attribute``, or ``approval_decision``, and tests that
    # rely on create_all (rather than running alembic) won't see those
    # tables at all. The matching layer-2 retrieval models are pulled in
    # by ``alembic/env.py`` only — keeping the import here so the same
    # ``create_all`` path works in tests.
    import layer2.compliance.db.models  # noqa: F401

    Base.metadata.create_all(make_engine(db_url))
