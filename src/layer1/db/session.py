from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from layer1.config import get_settings


def make_engine(db_url: str | None = None):
    return create_engine(db_url or get_settings().database_url, future=True)


def make_session_factory(db_url: str | None = None) -> sessionmaker[Session]:
    return sessionmaker(bind=make_engine(db_url), expire_on_commit=False, future=True)


@contextmanager
def session_scope(db_url: str | None = None) -> Iterator[Session]:
    factory = make_session_factory(db_url)
    session = factory()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
