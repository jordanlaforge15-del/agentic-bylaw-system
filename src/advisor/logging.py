"""Structured JSON logging with correlation-ID propagation.

Provides:
* ``setup_logging(level, json_output)`` — call once at process startup
  to configure the root logger with either a human-readable or
  machine-readable (JSON) formatter.
* ``correlation_id`` — a ``contextvars.ContextVar`` that, when set by
  the ``CorrelationIdMiddleware``, is automatically injected into every
  log record as ``correlation_id``.
* ``CorrelationIdMiddleware`` — Starlette middleware that generates a
  UUID-4 per request, stores it in ``correlation_id``, and echoes it
  back via the ``X-Correlation-ID`` response header.

Environment knobs (all optional, read at ``setup_logging`` call time):
    ``LOG_LEVEL``  — Python level name; default ``INFO``.
    ``LOG_FORMAT`` — ``"json"`` (default in production) or ``"text"``.
"""
from __future__ import annotations

import contextvars
import json
import logging
import os
import time
import uuid
from datetime import datetime, timezone
from typing import Any

from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import Response

correlation_id: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "correlation_id", default=None
)


class _CorrelationIdFilter(logging.Filter):
    """Inject ``correlation_id`` from the context var into every record."""

    def filter(self, record: logging.LogRecord) -> bool:
        record.correlation_id = correlation_id.get()  # type: ignore[attr-defined]
        return True


class JsonFormatter(logging.Formatter):
    """Emit each log record as a single JSON line.

    Fields: ``timestamp``, ``level``, ``logger``, ``message``,
    ``correlation_id``, and any ``exc_info`` (rendered as ``exception``).
    """

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "timestamp": datetime.fromtimestamp(record.created, tz=timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            "correlation_id": getattr(record, "correlation_id", None),
        }
        if record.exc_info and record.exc_info[1] is not None:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(payload, default=str)


class TextFormatter(logging.Formatter):
    """Human-readable formatter that includes the correlation ID."""

    FORMAT = "[%(asctime)s] %(levelname)s %(name)s [%(correlation_id)s] %(message)s"

    def __init__(self) -> None:
        super().__init__(fmt=self.FORMAT, datefmt="%Y-%m-%d %H:%M:%S")


def setup_logging(
    level: str | None = None,
    json_output: bool | None = None,
) -> None:
    """Configure the root logger for the advisor process.

    Parameters
    ----------
    level:
        Python log-level name (``DEBUG``, ``INFO``, …). Falls back to
        the ``LOG_LEVEL`` env var, then to ``INFO``.
    json_output:
        ``True`` → JSON lines, ``False`` → human text. Falls back to
        ``LOG_FORMAT`` env var (``"json"`` / ``"text"``), then to
        ``True`` (JSON is the production default so Docker/Loki can
        parse logs without a custom regex).
    """
    resolved_level = (level or os.environ.get("LOG_LEVEL", "INFO")).upper()

    if json_output is None:
        env_fmt = os.environ.get("LOG_FORMAT", "json").lower()
        json_output = env_fmt != "text"

    root = logging.getLogger()
    root.setLevel(resolved_level)

    # Remove any existing handlers to avoid duplicate output when
    # setup_logging is called more than once (e.g. in tests).
    for handler in root.handlers[:]:
        root.removeHandler(handler)

    handler = logging.StreamHandler()
    handler.addFilter(_CorrelationIdFilter())
    handler.setFormatter(JsonFormatter() if json_output else TextFormatter())
    root.addHandler(handler)

    # Quiet noisy third-party loggers that flood at DEBUG/INFO.
    for name in ("httpx", "httpcore", "uvicorn.access", "hpack"):
        logging.getLogger(name).setLevel(max(logging.WARNING, root.level))


class CorrelationIdMiddleware(BaseHTTPMiddleware):
    """Generate a per-request correlation ID and propagate it."""

    HEADER = "X-Correlation-ID"

    async def dispatch(
        self, request: Request, call_next: RequestResponseEndpoint
    ) -> Response:
        # Honour an upstream correlation ID if provided (e.g. from the
        # Next.js proxy or a load balancer), otherwise mint a new one.
        cid = request.headers.get(self.HEADER) or uuid.uuid4().hex
        token = correlation_id.set(cid)
        try:
            response = await call_next(request)
            response.headers[self.HEADER] = cid
            return response
        finally:
            correlation_id.reset(token)
