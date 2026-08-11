"""Unit tests for scripts/run_test_prompts.py.

Focus: ABS-459 — ``tool_calls`` must reflect what the tool loop actually
dispatched, not what the synthetic SSE content stream happens to carry.

Background. ``advisor.chat.session`` builds the SSE content stream from the
tool loop's *final* response, so it never contains ``tool_use`` blocks: the
loop has already settled to ``end_turn`` before streaming starts. Harvesting
tool calls from ``content_block_start`` therefore yields nothing on every
backend. ABS-266's ``tool_loop_metrics`` event is the only record of the
loop's internals, and is the fallback source.

Does NOT require a running advisor stack or database.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from scripts.run_test_prompts import extract_turn_artifacts


REPO_ROOT = Path(__file__).resolve().parents[2]


def _event(name: str, data: dict) -> dict:
    return {"event": name, "data": data}


def _metrics_event(names: list[str], *, iterations: int = 1) -> dict:
    """A tool_loop_metrics SSE event naming the calls the loop dispatched."""
    return _event(
        "tool_loop_metrics",
        {
            "type": "tool_loop_metrics",
            "iterations": iterations,
            "terminated_reason": "end_turn",
            "tool_calls": [
                {"name": n, "is_error": False, "latency_ms": 120} for n in names
            ],
            "per_iteration": [
                {"iteration": 1, "tool_call_count": len(names), "latency_ms": 900}
            ],
        },
    )


def _text_stream(text: str = "answer") -> list[dict]:
    """The content stream a real turn produces: text blocks only."""
    return [
        _event("message_start", {"model": "claude-opus-4-5"}),
        _event(
            "content_block_start",
            {"index": 0, "content_block": {"type": "text", "text": text}},
        ),
        _event("content_block_stop", {"index": 0}),
        _event("message_delta", {"stop_reason": "end_turn"}),
    ]


# ---------------------------------------------------------------------------
# Content stream carries tool_use blocks (hypothetical richer backend)
# ---------------------------------------------------------------------------


def test_content_stream_tool_use_blocks_win_over_metrics():
    """When the stream does carry tool_use blocks, keep them — they have inputs.

    Guards the forward path: a backend that someday streams real tool_use
    blocks must not have its richer data replaced by the metrics fallback.
    """
    events = [
        _event("message_start", {"model": "m"}),
        _event(
            "content_block_start",
            {
                "index": 0,
                "content_block": {
                    "type": "tool_use",
                    "id": "toolu_1",
                    "name": "search_bylaw_evidence",
                    "input": {"query": "rear setback"},
                },
            },
        ),
        _event("content_block_stop", {"index": 0}),
        # Metrics disagree on purpose: they must be ignored here.
        _metrics_event(["get_zone_profile", "lookup_citation"]),
        _event("message_delta", {"stop_reason": "end_turn"}),
    ]

    result = extract_turn_artifacts(events)

    assert [c["name"] for c in result["tool_calls"]] == ["search_bylaw_evidence"]
    assert result["tool_calls"][0]["input"] == {"query": "rear setback"}
    assert result["tool_calls"][0]["id"] == "toolu_1"
    assert "source" not in result["tool_calls"][0]


# ---------------------------------------------------------------------------
# The real-world path: text-only stream + tool_loop_metrics
# ---------------------------------------------------------------------------


def test_falls_back_to_tool_loop_metrics_when_stream_has_no_tool_use():
    """The actual production shape — this is the ABS-459 regression."""
    names = ["search_bylaw_evidence", "get_address_profile", "lookup_citation"]
    events = _text_stream() + [_metrics_event(names, iterations=3)]

    result = extract_turn_artifacts(events)

    assert [c["name"] for c in result["tool_calls"]] == names
    assert all(c["source"] == "tool_loop_metrics" for c in result["tool_calls"])
    assert all(c["input"] is None for c in result["tool_calls"])
    assert all(c["is_error"] is False for c in result["tool_calls"])
    # The metrics event itself is still captured verbatim for downstream use.
    assert result["tool_loop_metrics"]["iterations"] == 3


def test_error_state_and_latency_survive_the_fallback():
    events = _text_stream() + [
        _event(
            "tool_loop_metrics",
            {
                "type": "tool_loop_metrics",
                "iterations": 1,
                "tool_calls": [
                    {"name": "lookup_citation", "is_error": True, "latency_ms": 42}
                ],
            },
        )
    ]

    call = extract_turn_artifacts(events)["tool_calls"][0]

    assert call["is_error"] is True
    assert call["latency_ms"] == 42


# ---------------------------------------------------------------------------
# Degenerate inputs must not crash the runner mid-suite
# ---------------------------------------------------------------------------


def test_no_tool_use_and_no_metrics_yields_empty_list():
    result = extract_turn_artifacts(_text_stream())
    assert result["tool_calls"] == []


def test_metrics_present_but_empty_tool_calls_yields_empty_list():
    events = _text_stream() + [_metrics_event([])]
    assert extract_turn_artifacts(events)["tool_calls"] == []


@pytest.mark.parametrize("junk", [None, "not-a-dict", 17, []])
def test_malformed_metric_entries_are_skipped(junk):
    """A 20-case run must not die on one malformed metrics payload."""
    events = _text_stream() + [
        _event(
            "tool_loop_metrics",
            {
                "type": "tool_loop_metrics",
                "tool_calls": [junk, {"name": "search_bylaw_evidence"}],
            },
        )
    ]

    result = extract_turn_artifacts(events)

    assert [c["name"] for c in result["tool_calls"]] == ["search_bylaw_evidence"]


# ---------------------------------------------------------------------------
# The committed transcript that exposed the bug
# ---------------------------------------------------------------------------


def test_committed_tc001_transcript_reparses_to_sixteen_tool_calls():
    """DoD #2: re-deriving from the committed run must yield 16, not 0.

    ``evals/runs/20260811T113204Z/TC-001.json`` was produced by the buggy
    parser: every turn carries ``tool_calls: []`` while its own
    ``tool_loop_metrics`` records 16 dispatched calls across 8 iterations.
    Re-deriving through the fixed fallback must recover all 16.
    """
    transcript = (
        REPO_ROOT / "evals" / "runs" / "20260811T113204Z" / "TC-001.json"
    )
    if not transcript.exists():
        pytest.skip(f"fixture run not present: {transcript}")

    doc = json.loads(transcript.read_text())

    recovered = 0
    for turn in doc["turns"]:
        metrics = turn.get("tool_loop_metrics") or {}
        events = _text_stream(turn.get("assistant_text") or "") + [
            _event("tool_loop_metrics", metrics)
        ]
        recovered += len(extract_turn_artifacts(events)["tool_calls"])

    assert recovered == 16
    # And the transcript as committed shows the bug it was captured under.
    assert all(t["tool_calls"] == [] for t in doc["turns"])
