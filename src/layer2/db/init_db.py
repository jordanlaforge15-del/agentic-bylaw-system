from __future__ import annotations

from layer1.db.base import Base
from layer1.db.session import make_engine
import layer2.db.models  # noqa: F401


def create_all(db_url: str | None = None) -> None:
    Base.metadata.create_all(make_engine(db_url))
