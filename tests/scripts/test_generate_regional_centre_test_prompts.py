"""Tests for the `claude -p` swap in scripts/generate_regional_centre_test_prompts.py.

ABS-262: the generator now shells out to the `claude` CLI (Claude Code headless
mode) instead of calling `anthropic.messages.create`. These tests pin three
behaviours:

1. A successful `claude -p` run returns `list[dict]` with the {turn, role, message}
   schema downstream consumers (`build_test_case`, `evals/regional_centre_test_prompts.json`)
   depend on.
2. The arguments passed to `subprocess.run` actually invoke `claude -p` with the
   generated prompt content — we don't silently regress to the old transport.
3. When the `claude` binary is missing from PATH, the function falls back to the
   stub generator instead of raising or hanging.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

# Ensure the repo root is on sys.path so `scripts.generate_regional_centre_test_prompts`
# imports cleanly regardless of pytest invocation cwd.
REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from scripts import generate_regional_centre_test_prompts as gen  # noqa: E402


SPEC = {
    "zone": "CEN-1",
    "persona": "real_estate_developer",
    "complexity": "complex",
    "liability": "high",
    "tags": ["new_construction"],
    "bylaw_features": ["FAR", "height_overlay"],
    "address": "1500 Argyle Street, Halifax, NS",
    "title": "Developer tower feasibility in CEN-1",
    "turns": 2,
}

CANNED_TURNS = [
    {"turn": 1, "role": "user", "message": "I'm scoping a mixed-use tower at 1500 Argyle Street in CEN-1. What's the maximum FAR?"},
    {"turn": 2, "role": "user", "message": "Does the height overlay change anything on that block?"},
]


def _fake_completed_process(stdout: str, returncode: int = 0, stderr: str = "") -> subprocess.CompletedProcess:
    return subprocess.CompletedProcess(
        args=["claude", "-p"],
        returncode=returncode,
        stdout=stdout,
        stderr=stderr,
    )


def test_generate_turns_via_api_returns_correct_shape(monkeypatch: pytest.MonkeyPatch) -> None:
    """Happy path: claude -p returns a JSON array; we get list[dict] with the right keys."""
    monkeypatch.setattr(gen.shutil, "which", lambda _binary: "/usr/local/bin/claude")
    monkeypatch.setattr(
        gen.subprocess,
        "run",
        lambda *a, **kw: _fake_completed_process(json.dumps(CANNED_TURNS)),
    )

    turns = gen.generate_turns_via_api(SPEC)

    assert isinstance(turns, list)
    assert len(turns) == 2
    for t in turns:
        assert set(t.keys()) >= {"turn", "role", "message"}
        assert t["role"] == "user"
        assert isinstance(t["turn"], int)
        assert isinstance(t["message"], str)


def test_generate_turns_via_api_invokes_claude_p_with_prompt(monkeypatch: pytest.MonkeyPatch) -> None:
    """The subprocess call must literally be `claude -p <prompt>` (with the system prompt appended)."""
    monkeypatch.setattr(gen.shutil, "which", lambda _binary: "/usr/local/bin/claude")

    captured: dict = {}

    def fake_run(cmd, **kwargs):
        captured["cmd"] = cmd
        captured["kwargs"] = kwargs
        return _fake_completed_process(json.dumps(CANNED_TURNS))

    monkeypatch.setattr(gen.subprocess, "run", fake_run)

    gen.generate_turns_via_api(SPEC)

    cmd = captured["cmd"]
    assert cmd[0] == "claude"
    assert "-p" in cmd
    # The prompt body should be on the command line and should reference the spec details.
    prompt_arg = cmd[2]  # claude -p <prompt> ...
    assert "CEN-1" in prompt_arg
    assert "real_estate_developer" in prompt_arg
    assert "1500 Argyle Street" in prompt_arg
    # System prompt is forwarded via --append-system-prompt
    assert "--append-system-prompt" in cmd
    sys_idx = cmd.index("--append-system-prompt")
    assert "test-case author" in cmd[sys_idx + 1]
    # Capture stdout/stderr for parsing
    assert captured["kwargs"].get("capture_output") is True
    assert captured["kwargs"].get("text") is True


def test_generate_turns_via_api_falls_back_to_stub_when_claude_missing(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """If `claude` is not on PATH, fall back to stub instead of raising."""
    monkeypatch.setattr(gen.shutil, "which", lambda _binary: None)

    # Guard: subprocess.run must NOT be called in the fallback path.
    def _no_subprocess(*a, **kw):
        raise AssertionError("subprocess.run should not be called when claude CLI is missing")

    monkeypatch.setattr(gen.subprocess, "run", _no_subprocess)

    turns = gen.generate_turns_via_api(SPEC)

    assert isinstance(turns, list)
    assert len(turns) == SPEC["turns"]
    # Stub messages are marked with the [STUB ...] sentinel.
    assert all("[STUB" in t["message"] for t in turns)

    err = capsys.readouterr().err
    assert "claude" in err.lower()


def test_generate_turns_via_api_tolerates_markdown_fences(monkeypatch: pytest.MonkeyPatch) -> None:
    """claude -p sometimes wraps JSON in ```json fences — parser must strip them."""
    monkeypatch.setattr(gen.shutil, "which", lambda _binary: "/usr/local/bin/claude")
    fenced = "```json\n" + json.dumps(CANNED_TURNS) + "\n```"
    monkeypatch.setattr(
        gen.subprocess,
        "run",
        lambda *a, **kw: _fake_completed_process(fenced),
    )

    turns = gen.generate_turns_via_api(SPEC)
    assert turns == CANNED_TURNS


def test_generate_turns_via_api_raises_on_nonzero_exit(monkeypatch: pytest.MonkeyPatch) -> None:
    """A failing `claude -p` must raise loudly rather than poisoning the corpus."""
    monkeypatch.setattr(gen.shutil, "which", lambda _binary: "/usr/local/bin/claude")
    monkeypatch.setattr(
        gen.subprocess,
        "run",
        lambda *a, **kw: _fake_completed_process("", returncode=2, stderr="boom"),
    )

    with pytest.raises(RuntimeError, match="claude -p failed"):
        gen.generate_turns_via_api(SPEC)
