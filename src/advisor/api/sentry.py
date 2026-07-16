"""Sentry error-tracking integration for the FastAPI advisor backend.

Reads ``SENTRY_DSN`` from the environment at init time. When the DSN is
absent or empty, ``sentry_sdk.init()`` is never called and the SDK
stays inert — all ``capture_*`` calls silently no-op. This keeps the
local-dev and e2e paths zero-config.
"""
from __future__ import annotations

import logging
import os

logger = logging.getLogger(__name__)

_initialized: bool = False


def init_sentry() -> None:
    """Initialise the Sentry SDK if ``SENTRY_DSN`` is set.

    Safe to call more than once — subsequent calls are no-ops.
    """
    global _initialized  # noqa: PLW0603
    if _initialized:
        return

    dsn = os.environ.get("SENTRY_DSN", "").strip()
    if not dsn:
        logger.info(
            "SENTRY_DSN is not set — Sentry error tracking is disabled. "
            "Set SENTRY_DSN to enable production error reporting."
        )
        _initialized = True
        return

    import sentry_sdk  # noqa: PLC0415 — lazy so non-advisor installs skip the dep

    sentry_sdk.init(
        dsn=dsn,
        environment=os.environ.get("SENTRY_ENVIRONMENT", "production"),
        release=os.environ.get("SENTRY_RELEASE"),
        traces_sample_rate=float(
            os.environ.get("SENTRY_TRACES_SAMPLE_RATE", "0.1")
        ),
        send_default_pii=False,
    )
    logger.info("Sentry error tracking initialised (env=%s)", os.environ.get("SENTRY_ENVIRONMENT", "production"))
    _initialized = True


def is_sentry_enabled() -> bool:
    """Return whether the Sentry SDK is capturing events."""
    try:
        import sentry_sdk  # noqa: PLC0415

        return sentry_sdk.is_initialized()
    except Exception:  # noqa: BLE001
        return False
