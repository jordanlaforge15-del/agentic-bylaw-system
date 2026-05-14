"""System-prompt loader for the chat assistant.

The persona lives in ``docs/agent/persona.md`` so non-engineers can edit
it without touching code. The file's preamble (everything before the
first ``---`` horizontal rule on its own line) is install /
maintenance instructions for engineers; only the prose AFTER the rule
is loaded into the LLM context.

The loader is cached with ``lru_cache`` because the persona is static
for the life of the process — if you edit it during development,
restart the server. (Production deploys re-read it on boot.)
"""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path

# Resolve ``docs/agent/persona.md`` relative to the repository root.
# The package lives at ``<repo>/src/advisor/chat/persona.py``; four
# parents up gets us back to ``<repo>``. We do this rather than
# ``Path.cwd()`` because pytest runs with arbitrary working dirs.
_REPO_ROOT = Path(__file__).resolve().parents[3]
_DEFAULT_PERSONA_PATH = _REPO_ROOT / "docs" / "agent" / "persona.md"
_DEFAULT_CLASSIFIER_PERSONA_PATH = (
    _REPO_ROOT / "docs" / "agent" / "classifier-persona.md"
)

# Sentinel used to split install instructions from the system prompt.
# Must match a line that is exactly ``---`` (no surrounding whitespace
# allowed beyond the trailing newline) — keeps the rule unambiguous.
_DIVIDER = "---"


@lru_cache(maxsize=1)
def load_persona(path: Path | None = None) -> str:
    """Return the system-prompt portion of the persona file.

    The file's preamble (engineering install instructions) is stripped:
    we return everything after the first line whose content is exactly
    ``---``. The result is the LLM-facing persona text, leading and
    trailing whitespace removed.

    Raises ``FileNotFoundError`` with a clear message if the persona
    file is missing — chat cannot start without a system prompt and
    this is a deployment/config error worth surfacing loudly.
    """
    persona_path = Path(path) if path is not None else _DEFAULT_PERSONA_PATH
    if not persona_path.exists():
        raise FileNotFoundError(
            f"Persona file not found at {persona_path}. The chat backend "
            "requires this file to populate the system prompt; create it "
            "or override the path via load_persona(path=...)."
        )

    raw = persona_path.read_text(encoding="utf-8")
    body = _strip_install_preamble(raw)
    return body.strip()


@lru_cache(maxsize=1)
def load_classifier_persona(path: Path | None = None) -> str:
    """Return the system-prompt portion of the classifier persona file.

    Mirror of ``load_persona`` for the Layer-2 pre-flight tier
    classifier. Same divider convention (everything before ``---`` is
    engineering preamble; everything after is what the model sees).
    Cached for the same lifecycle reasons as the main persona — restart
    the server after editing.
    """
    persona_path = (
        Path(path) if path is not None else _DEFAULT_CLASSIFIER_PERSONA_PATH
    )
    if not persona_path.exists():
        raise FileNotFoundError(
            f"Classifier persona file not found at {persona_path}. The "
            "Layer-2 classifier requires this file; create it or "
            "override the path via load_classifier_persona(path=...)."
        )

    raw = persona_path.read_text(encoding="utf-8")
    body = _strip_install_preamble(raw)
    return body.strip()


def _strip_install_preamble(text: str) -> str:
    """Drop everything up to and including the first ``---`` divider.

    If no divider is present the entire file is returned as-is — that
    keeps a freshly-written persona usable while the convention is
    being adopted, and avoids silent emptiness when the convention is
    forgotten.
    """
    lines = text.splitlines()
    for index, line in enumerate(lines):
        if line.strip() == _DIVIDER:
            return "\n".join(lines[index + 1 :])
    return text
