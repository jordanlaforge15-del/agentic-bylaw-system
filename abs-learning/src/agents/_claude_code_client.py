"""Re-export of the canonical Claude Code shim that now lives in the
installed layer1 package.

The shim was moved to ``src/layer1/_claude_code_client.py`` so that
non-abs-learning callers (e.g. ``src/layer1/pipeline/audit.py``) can
``from layer1._claude_code_client import ...`` without depending on
abs-learning being on the Python path. abs-learning callers continue
to import from this module unchanged — everything is re-exported.

See ``src/layer1/_claude_code_client.py`` for the implementation and
the full design rationale.
"""
from __future__ import annotations

from layer1._claude_code_client import (  # noqa: F401
    CLAUDE_CLI,
    DEFAULT_MAX_RETRIES,
    DEFAULT_TIMEOUT_S,
    ClaudeCodeBackendError,
    ClaudeCodeClient,
    call_claude_p_with_schema,
)
