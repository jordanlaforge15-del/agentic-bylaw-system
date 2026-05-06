"""Persona loader: strips engineering preamble, caches the result."""
from __future__ import annotations

from pathlib import Path

import pytest

from advisor.chat import persona as persona_module
from advisor.chat.persona import load_persona


@pytest.fixture(autouse=True)
def _clear_persona_cache():
    """The loader is ``lru_cache``d. Clear before AND after each test
    so the cache doesn't leak state across the suite (or back into
    itself when one test calls load_persona() with a custom path and
    a later test relies on the default)."""
    load_persona.cache_clear()
    yield
    load_persona.cache_clear()


def test_load_persona_returns_system_prompt_after_divider():
    """The shipped persona file's system prompt section starts with
    'You are a senior urban planner'. We pin that wording so a
    careless edit to the preamble (above the ``---`` divider) doesn't
    accidentally bleed into the LLM's system prompt."""
    text = load_persona()
    assert text.startswith("You are a senior urban planner")


def test_load_persona_excludes_install_preamble():
    """The preamble (engineering instructions) must not be included
    in the system prompt — the LLM should never see strings like
    'install instructions' or '(ignored by `load_persona`)' which
    would confuse it about its role."""
    text = load_persona()
    assert "Install instructions" not in text
    assert "ignored by" not in text


def test_load_persona_caches_result(tmp_path: Path):
    """Two calls with no path argument return the SAME object id —
    the cache is alive. We don't bother counting filesystem hits;
    object identity is the cleanest signal that the cache fired."""
    first = load_persona()
    second = load_persona()
    assert first is second


def test_load_persona_missing_file_raises_filenotfound(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """A misconfigured deployment must fail loudly rather than fall
    back to an empty system prompt — that would silently break the
    chat assistant."""
    bogus_path = tmp_path / "no-such-persona.md"
    # We point the module-level default at a non-existent file. The
    # cache is cleared by the fixture so this re-runs the disk
    # check.
    monkeypatch.setattr(persona_module, "_DEFAULT_PERSONA_PATH", bogus_path)
    with pytest.raises(FileNotFoundError, match="no-such-persona.md"):
        load_persona()


def test_load_persona_with_custom_path(tmp_path: Path):
    """Tests can pass an explicit path; the loader splits on the
    first ``---`` divider regardless of where the file lives."""
    custom = tmp_path / "custom.md"
    custom.write_text(
        "# Engineer notes\nblah\n---\nYou are a senior urban planner who knows things.\n"
    )
    # Note: passing ``path=`` bypasses the cache (path is part of the
    # cache key), so this won't pollute the default-path cache.
    text = load_persona(path=custom)
    assert text == "You are a senior urban planner who knows things."


def test_load_persona_no_divider_returns_full_text(tmp_path: Path):
    """If a freshly-written persona forgets the divider, we fall
    back to returning the entire file rather than producing an
    empty prompt — empty prompts are silent failures."""
    custom = tmp_path / "no-divider.md"
    custom.write_text("You are a senior urban planner with no divider.\n")
    text = load_persona(path=custom)
    assert text.startswith("You are a senior urban planner")
