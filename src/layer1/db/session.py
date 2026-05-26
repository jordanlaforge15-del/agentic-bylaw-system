from __future__ import annotations

import threading
from collections.abc import Iterator
from contextlib import contextmanager

from sqlalchemy import Engine, create_engine
from sqlalchemy.orm import Session, sessionmaker

from layer1.config import get_settings

# Engines are expensive to construct (they allocate a connection pool).
# Cache by URL so every call to session_scope() within a process reuses
# the same pool rather than opening a fresh Postgres connection each
# time. Without this cache, 3 session_scope() calls per chat turn ×
# N concurrent workers means N×3 Postgres connection setups per second
# — enough to degrade SSE completion time under parallel e2e load and
# trigger WebKit's per-origin connection cap (ABS-6).
_engine_lock = threading.Lock()
_engine_cache: dict[str, Engine] = {}


def make_engine(db_url: str | None = None) -> Engine:
    url = db_url or get_settings().database_url
    if url not in _engine_cache:
        with _engine_lock:
            if url not in _engine_cache:
                _engine_cache[url] = create_engine(
                    url,
                    future=True,
                    pool_size=10,
                    max_overflow=5,
                )
    return _engine_cache[url]


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
