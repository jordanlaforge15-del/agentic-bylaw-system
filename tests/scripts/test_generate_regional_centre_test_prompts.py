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
import pathlib
import subprocess

import pytest

from scripts import generate_regional_centre_test_prompts as gen

# ABS-467: "address" is no longer an operator input — main() derives it from
# "zone" and writes it onto the spec before the prompt is built. These tests
# stand downstream of that, so the key is present but stands for a *derived*
# address, not a supplied one.
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


# ---------------------------------------------------------------------------
# ABS-467: the address is derived from the zone, not accepted alongside it
# ---------------------------------------------------------------------------


class _StubZoneAddress:
    """Shaped like scripts.zone_address_picker.ZoneAddress."""

    address = "5531 Nora Bernard Street, Halifax, NS"
    resolved_zone = "CEN-1"
    resolution_quality = "rooftop"
    location_type = "ROOFTOP"
    location_confidence = 0.95
    location_resolver = "google_maps"
    parcel_pid = "00155622"


def test_address_is_not_a_command_line_input() -> None:
    """The flag that let an operator assert an unchecked address is gone.

    This is the defect itself, not a stylistic preference: `--zone CEN-1
    --address "1505 Barrington Street"` produced TC-006, whose address is in
    DH. Removing the flag is what makes the mismatch unreachable.
    """
    source = pathlib.Path(gen.__file__).read_text()
    assert '"--address"' not in source
    assert '"--on-street"' in source, "the street preference replaces it"


def test_derive_address_raises_when_no_address_confirms_the_zone(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A zone with no confirmable address must fail loudly, not fall back.

    ER-1 is the live example: the by-law defines it but the schedule maps no
    polygon, so every candidate fails verification. Emitting a case anyway
    would grade the advisor against a zone the address is not in.
    """
    monkeypatch.setattr(gen, "pick_address_for_zone", lambda *a, **kw: None)
    with pytest.raises(RuntimeError, match="could be verified through the production path"):
        gen.derive_address(object(), {"zone": "ER-1"})


def test_build_test_case_records_the_resolution_it_was_verified_against() -> None:
    """The case carries its evidence, so an estimated point cannot pass as exact."""
    turns = [{"turn": 1, "role": "user", "message": "..."}]
    case = gen.build_test_case(SPEC, turns, "TC-021", _StubZoneAddress())

    assert case["address"] == _StubZoneAddress.address
    assert case["zone"] == "CEN-1"
    resolution = case["address_resolution"]
    assert resolution["resolved_zone"] == case["zone"]
    assert resolution["resolution_quality"] == "rooftop"
    assert resolution["location_type"] == "ROOFTOP"
    assert resolution["parcel_pid"] == "00155622"


def test_generation_prompt_pins_the_derived_address(monkeypatch: pytest.MonkeyPatch) -> None:
    """The model is told to use the derived address verbatim, not to invent one."""
    prompt = gen.build_generation_prompt(SPEC)
    assert SPEC["address"] in prompt
    assert "verbatim" in prompt


def test_er1_is_absent_from_the_selectable_zones() -> None:
    """--zone only offers zones the schedule actually maps."""
    assert "ER-1" not in gen.ZONES
    assert {"ER-2", "ER-3", "CEN-1", "RPK"} <= set(gen.ZONES)
    assert len(gen.ZONES) == 25, "the Regional Centre maps 25 zone codes"
