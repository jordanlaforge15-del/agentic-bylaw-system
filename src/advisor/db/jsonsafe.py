"""Coerce JSON-able structures into values a Postgres ``text``/``jsonb``
column will actually accept.

Two values are well-formed JSON yet rejected by a Postgres column, and
both can ride into a persisted payload from the real bylaw tools where
the mock/sqlite test paths never produce them:

* **NUL bytes** (``0x00``) in a string. The bylaw evidence is extracted
  from municipal PDFs, which routinely carry stray NULs; that NUL rides
  through a string ``tool_result`` (and the LLM's echoed answer) untouched
  by Pydantic — ``model_dump`` does not scrub strings. Postgres then
  rejects it on write: ``text fields cannot contain NUL (0x00) bytes`` for
  a ``text`` column, ``unsupported Unicode escape sequence … \\u0000
  cannot be converted to text`` for a ``jsonb`` column.
* **Non-finite floats** (``NaN`` / ``±Infinity``) — a ``0/0`` ratio or a
  divide-by-zero in a geometry tool yields one, and Postgres ``jsonb``
  rejects the bare ``NaN`` / ``Infinity`` tokens. (Pydantic's json-mode
  dump already nulls these inside typed model fields; we belt-and-brace
  any that arrive raw.)

These writes typically happen in a ``db.flush()`` *outside* an
engine-error guard, so either rejection surfaces as a raw HTTP 500 on
content the user already waited for. The fix is to scrub at the
persistence boundary.

This module is intentionally dependency-free (stdlib only) so any layer
that persists JSON — billing answers (ABS-332), chat-session storage
(ABS-333), or future writers — can import it without a circular-import
risk. See ``advisor.billing.answers`` and ``advisor.api.db_session_store``
for the two current call sites.
"""
from __future__ import annotations

import math
from typing import Any


def scrub_text(value: str) -> str:
    """Strip the one character Postgres ``text``/``jsonb`` cannot store.

    Only the NUL (``0x00``) is illegal in both column types; other control
    characters (tab, newline, carriage return) are legal and are left
    intact. The ``"\\x00" in value`` guard keeps the common no-NUL case
    allocation-free.
    """
    return value.replace("\x00", "") if "\x00" in value else value


def json_safe(value: Any) -> Any:
    """Recursively coerce a JSON-able structure into one Postgres accepts.

    Scrubs NUL bytes from every string (values *and* dict keys, see
    :func:`scrub_text`) and replaces non-finite floats (``NaN`` /
    ``±Infinity``) with ``None``. Lists and dicts are walked in place;
    scalars of any other type pass through untouched.
    """
    if isinstance(value, str):
        return scrub_text(value)
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if isinstance(value, dict):
        return {json_safe(k): json_safe(v) for k, v in value.items()}
    if isinstance(value, list):
        return [json_safe(v) for v in value]
    return value
