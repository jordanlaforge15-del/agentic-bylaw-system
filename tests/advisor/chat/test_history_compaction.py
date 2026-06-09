"""Unit tests for the history-compaction view used before LLM submit.

These tests exercise the compaction primitives directly (no gateway,
no tool loop) so each summary projection can be pinned without
worrying about session bookkeeping. The session-level integration
test (``test_session.py::test_compaction_*``) covers the end-to-end
wiring and the persistence-untouched guarantee.
"""
from __future__ import annotations

import json

import pytest

from advisor.chat.history_compaction import (
    compact_history_for_submission,
    compact_tool_loop_history,
    resolve_keep_recent,
    resolve_tool_loop_keep_recent,
)
from advisor.llm import (
    LLMRole,
    Message,
    TextBlock,
    ToolResultBlock,
    ToolUseBlock,
)


def _user_text(text: str) -> Message:
    return Message(role=LLMRole.USER, content=text)


def _assistant_tool_use(
    *, tool_id: str, tool_name: str, tool_input: dict, preamble: str | None = None
) -> Message:
    blocks: list = []
    if preamble:
        blocks.append(TextBlock(text=preamble))
    blocks.append(ToolUseBlock(id=tool_id, name=tool_name, input=tool_input))
    return Message(role=LLMRole.ASSISTANT, content=blocks)


def _tool_result(tool_use_id: str, content: str, is_error: bool = False) -> Message:
    return Message(
        role=LLMRole.USER,
        content=[
            ToolResultBlock(
                tool_use_id=tool_use_id,
                content=content,
                is_error=is_error,
            )
        ],
    )


def _assistant_text(text: str) -> Message:
    return Message(role=LLMRole.ASSISTANT, content=[TextBlock(text=text)])


# -- resolve_keep_recent ---------------------------------------------------


def test_resolve_keep_recent_explicit_field_wins(monkeypatch):
    monkeypatch.setenv("ADVISOR_CHAT_COMPACT_KEEP_RECENT", "5")
    assert resolve_keep_recent(3) == 3


def test_resolve_keep_recent_env_var_used_when_field_none(monkeypatch):
    monkeypatch.setenv("ADVISOR_CHAT_COMPACT_KEEP_RECENT", "4")
    assert resolve_keep_recent(None) == 4


def test_resolve_keep_recent_defaults_to_two(monkeypatch):
    monkeypatch.delenv("ADVISOR_CHAT_COMPACT_KEEP_RECENT", raising=False)
    assert resolve_keep_recent(None) == 2


def test_resolve_keep_recent_invalid_env_falls_back(monkeypatch):
    monkeypatch.setenv("ADVISOR_CHAT_COMPACT_KEEP_RECENT", "not-a-number")
    assert resolve_keep_recent(None) == 2


def test_resolve_keep_recent_clamps_to_one(monkeypatch):
    """A zero or negative resolved value would compact the live turn —
    that would strip the tool_result the model is actively answering
    from. Clamp to 1 so the in-flight turn is always intact."""
    monkeypatch.delenv("ADVISOR_CHAT_COMPACT_KEEP_RECENT", raising=False)
    assert resolve_keep_recent(0) == 1
    assert resolve_keep_recent(-3) == 1


# -- structural rules ------------------------------------------------------


def test_no_compaction_when_history_shorter_than_keep_recent():
    """One completed turn + an in-flight prompt is below the cutoff;
    the compacted view should equal the input message list."""
    payload = json.dumps({"matches": [], "total_matches": 0})
    messages = [
        _user_text("first question"),
        _assistant_tool_use(
            tool_id="tu_1", tool_name="search_bylaw_evidence", tool_input={"query": "x"}
        ),
        _tool_result("tu_1", payload),
        _assistant_text("first answer"),
        _user_text("second question"),
    ]
    out = compact_history_for_submission(messages, keep_recent=2)
    assert out == messages


def test_compaction_kicks_in_on_third_turn():
    """With keep_recent=2 and three user-prompt turns in the history,
    turn 1's tool_result content should be summarised but turn 2's
    must stay verbatim (it's part of the kept-recent window)."""
    payload_old = json.dumps(
        {
            "total_matches": 3,
            "matches": [
                {"citation_path": "4.2.1", "score": 0.9},
                {"citation_path": "4.2.3", "score": 0.85},
                {"citation_path": "5.1.7", "score": 0.80},
            ],
        }
    )
    payload_recent = json.dumps({"total_matches": 1, "matches": []})

    messages = [
        # Turn 1 — eligible for compaction.
        _user_text("q1"),
        _assistant_tool_use(
            tool_id="tu_1",
            tool_name="search_bylaw_evidence",
            tool_input={"query": "height limit ER-2"},
        ),
        _tool_result("tu_1", payload_old),
        _assistant_text("a1"),
        # Turn 2 — kept intact.
        _user_text("q2"),
        _assistant_tool_use(
            tool_id="tu_2",
            tool_name="search_bylaw_evidence",
            tool_input={"query": "lot coverage"},
        ),
        _tool_result("tu_2", payload_recent),
        _assistant_text("a2"),
        # Turn 3 — in-flight user prompt, no response yet.
        _user_text("q3"),
    ]
    out = compact_history_for_submission(messages, keep_recent=2)

    # Turn 1 tool_result is summarised.
    summarised = out[2].content[0]
    assert isinstance(summarised, ToolResultBlock)
    assert summarised.tool_use_id == "tu_1"
    text = summarised.content
    assert isinstance(text, str)
    assert text.startswith("[retrieved: 3 matches for 'height limit ER-2'")
    assert "citations 4.2.1/4.2.3/5.1.7" in text
    assert len(text) < len(payload_old)

    # Turn 2 tool_result is left verbatim.
    intact = out[6].content[0]
    assert isinstance(intact, ToolResultBlock)
    assert intact.content == payload_recent

    # tool_use blocks remain untouched in both turns.
    assert isinstance(out[1].content[-1], ToolUseBlock)
    assert isinstance(out[5].content[-1], ToolUseBlock)

    # Assistant text in turn 1 is preserved verbatim.
    assert out[3].content[0].text == "a1"


def test_compaction_does_not_mutate_input_list():
    payload = json.dumps({"total_matches": 1, "matches": [{"citation_path": "1.1"}]})
    messages = [
        _user_text("q1"),
        _assistant_tool_use(
            tool_id="tu_1", tool_name="search_bylaw_evidence", tool_input={"query": "x"}
        ),
        _tool_result("tu_1", payload),
        _assistant_text("a1"),
        _user_text("q2"),
        _assistant_text("a2"),
        _user_text("q3"),
    ]
    snapshot = json.dumps(
        [m.model_dump(mode="json") for m in messages], sort_keys=True
    )
    compact_history_for_submission(messages, keep_recent=2)
    after = json.dumps(
        [m.model_dump(mode="json") for m in messages], sort_keys=True
    )
    assert snapshot == after


def test_compaction_is_deterministic():
    """Same input must produce byte-identical bytes — that's the
    requirement for Anthropic prompt-cache prefix stability."""
    payload = json.dumps(
        {
            "total_matches": 2,
            "matches": [
                {
                    "citation_path": "4.2.1",
                    "score": 0.94,
                    "linked_datasets": [{"location_confidence": 0.92}],
                },
                {
                    "citation_path": "4.2.3",
                    "score": 0.80,
                    "linked_datasets": [{"location_confidence": 0.71}],
                },
            ],
        }
    )
    messages = [
        _user_text("q1"),
        _assistant_tool_use(
            tool_id="tu_1",
            tool_name="search_bylaw_evidence",
            tool_input={
                "query": "max height",
                "location": {"civic_number": "6321", "street": "Quinpool Road"},
            },
        ),
        _tool_result("tu_1", payload),
        _assistant_text("a1"),
        _user_text("q2"),
        _assistant_text("a2"),
        _user_text("q3"),
    ]
    a = compact_history_for_submission(messages, keep_recent=2)
    b = compact_history_for_submission(messages, keep_recent=2)
    a_bytes = json.dumps([m.model_dump(mode="json") for m in a], sort_keys=True)
    b_bytes = json.dumps([m.model_dump(mode="json") for m in b], sort_keys=True)
    assert a_bytes == b_bytes


def test_error_tool_results_are_summarised_with_error_marker():
    messages = [
        _user_text("q1"),
        _assistant_tool_use(
            tool_id="tu_err",
            tool_name="search_bylaw_evidence",
            tool_input={"query": "x"},
        ),
        _tool_result("tu_err", "ValidationError: query is required", is_error=True),
        _assistant_text("a1"),
        _user_text("q2"),
        _assistant_text("a2"),
        _user_text("q3"),
    ]
    out = compact_history_for_submission(messages, keep_recent=2)
    err_block = out[2].content[0]
    assert isinstance(err_block, ToolResultBlock)
    assert err_block.is_error is True
    assert "error" in err_block.content.lower()


def test_unparseable_tool_result_falls_back_to_truncated_excerpt():
    """A tool that doesn't return JSON (or has malformed JSON) should
    still get a bounded summary — we never re-ship the raw payload."""
    junk = "this is not json " + ("x" * 5000)
    messages = [
        _user_text("q1"),
        _assistant_tool_use(
            tool_id="tu_x", tool_name="search_bylaw_evidence", tool_input={"query": "x"}
        ),
        _tool_result("tu_x", junk),
        _assistant_text("a1"),
        _user_text("q2"),
        _assistant_text("a2"),
        _user_text("q3"),
    ]
    out = compact_history_for_submission(messages, keep_recent=2)
    summarised = out[2].content[0]
    assert isinstance(summarised, ToolResultBlock)
    assert isinstance(summarised.content, str)
    assert len(summarised.content) < 250


# -- per-tool projections --------------------------------------------------


def _summary_with_input(tool_name: str, tool_input: dict, payload: dict) -> str:
    """Run the compaction pipeline on a single tool_result so we can
    assert on the resulting summary string directly."""
    messages = [
        _user_text("q1"),
        _assistant_tool_use(
            tool_id="tu_1", tool_name=tool_name, tool_input=tool_input
        ),
        _tool_result("tu_1", json.dumps(payload)),
        _assistant_text("a1"),
        _user_text("q2"),
        _assistant_text("a2"),
        _user_text("q3"),
    ]
    out = compact_history_for_submission(messages, keep_recent=2)
    block = out[2].content[0]
    assert isinstance(block, ToolResultBlock)
    assert isinstance(block.content, str)
    return block.content


def test_search_summary_matches_brief_format():
    summary = _summary_with_input(
        "search_bylaw_evidence",
        {"query": "height limit ER-2"},
        {
            "total_matches": 3,
            "matches": [
                {
                    "citation_path": "4.2.1",
                    "score": 0.94,
                    "linked_datasets": [{"location_confidence": 0.94}],
                },
                {"citation_path": "4.2.3", "score": 0.91},
                {"citation_path": "5.1.7", "score": 0.80},
            ],
        },
    )
    # The brief's example: "[retrieved: 3 matches for 'height limit ER-2',
    # citations 4.2.1/4.2.3/5.1.7, max-confidence 0.94]". We allow extra
    # trailing fields (e.g. max-score) but require the load-bearing
    # parts.
    assert summary.startswith(
        "[retrieved: 3 matches for 'height limit ER-2',"
    )
    assert "citations 4.2.1/4.2.3/5.1.7" in summary
    assert "max-confidence 0.94" in summary


def test_search_summary_includes_location_when_provided():
    summary = _summary_with_input(
        "search_bylaw_evidence",
        {
            "query": "max height",
            "location": {"civic_number": "6321", "street": "Quinpool Road"},
        },
        {"total_matches": 1, "matches": [{"citation_path": "4.2.1"}]},
    )
    assert "location 6321 Quinpool Road" in summary


def test_search_summary_caps_citation_list():
    payload_matches = [
        {"citation_path": f"sec.{i}", "score": 0.5} for i in range(12)
    ]
    summary = _summary_with_input(
        "search_bylaw_evidence",
        {"query": "x"},
        {"total_matches": 12, "matches": payload_matches},
    )
    # Cap is 8; remaining count surfaces as "+N".
    assert "sec.0/sec.1/sec.2/sec.3/sec.4/sec.5/sec.6/sec.7/+4" in summary


def test_lookup_citation_summary_keeps_handles():
    summary = _summary_with_input(
        "lookup_citation",
        {"citation_path": "4.2.1"},
        {
            "citation_path": "4.2.1",
            "municipality": "HRM",
            "bylaw_name": "Land Use Bylaw",
            "page_start": 12,
            "page_end": 13,
            "text": "x" * 240,
        },
    )
    assert summary.startswith("[lookup_citation '4.2.1'")
    assert "HRM / Land Use Bylaw" in summary
    assert "p.12-13" in summary
    assert "240 chars" in summary


def test_outline_summary_lists_fragment_count():
    summary = _summary_with_input(
        "get_document_outline",
        {"document_id": 5},
        {
            "document": {"id": 5, "bylaw_name": "HRM LUB"},
            "fragments": [{"page_start": 1}] * 47,
        },
    )
    assert "doc=5" in summary
    assert "HRM LUB" in summary
    assert "47 fragments" in summary


def test_document_list_summary_breaks_down_by_municipality():
    summary = _summary_with_input(
        "list_documents",
        {},
        {
            "documents": [
                {"municipality": "HRM"},
                {"municipality": "HRM"},
                {"municipality": "HRM"},
                {"municipality": "Truro"},
            ]
        },
    )
    assert "3 documents" in summary or "4 documents" in summary
    assert "HRM=3" in summary
    assert "Truro=1" in summary


def test_keep_recent_one_compacts_everything_before_live_turn():
    payload = json.dumps({"total_matches": 1, "matches": [{"citation_path": "1.1"}]})
    messages = [
        _user_text("q1"),
        _assistant_tool_use(
            tool_id="tu_1", tool_name="search_bylaw_evidence", tool_input={"query": "a"}
        ),
        _tool_result("tu_1", payload),
        _assistant_text("a1"),
        _user_text("q2"),
        _assistant_tool_use(
            tool_id="tu_2", tool_name="search_bylaw_evidence", tool_input={"query": "b"}
        ),
        _tool_result("tu_2", payload),
        _assistant_text("a2"),
        _user_text("q3"),
    ]
    out = compact_history_for_submission(messages, keep_recent=1)
    # With keep_recent=1, only turn 3 (the in-flight prompt) is
    # protected — both prior tool_results are summarised.
    assert isinstance(out[2].content[0], ToolResultBlock)
    assert out[2].content[0].content.startswith("[retrieved:")
    assert isinstance(out[6].content[0], ToolResultBlock)
    assert out[6].content[0].content.startswith("[retrieved:")


@pytest.mark.parametrize(
    "location_input,expected",
    [
        (
            {"civic_number": "100", "street": "Main"},
            "100 Main",
        ),
        (
            {"civic_number": "100", "street": "Main", "unit": "B"},
            "100 Main (unit B)",
        ),
        ({"parcel_id": "00012345"}, "PID 00012345"),
        ({"named_place": "Halifax Citadel"}, "Halifax Citadel"),
        (
            {"intersection_streets": ["Spring Garden", "Queen"]},
            "Spring Garden & Queen",
        ),
        ({"geometry": {"type": "Point", "coordinates": [0, 0]}}, "GeoJSON"),
    ],
)
def test_location_label_shapes(location_input, expected):
    summary = _summary_with_input(
        "search_bylaw_evidence",
        {"query": "x", "location": location_input},
        {"total_matches": 0, "matches": []},
    )
    assert f"location {expected}" in summary


# -- WI-4 (ABS-290): in-flight tool-loop compaction -----------------------


def _search_round(idx: int, *, citation: str) -> list[Message]:
    """One in-loop search round: assistant tool_use + the user-side
    tool_result it produced. Mirrors the shape ``run_tool_loop`` builds
    inside a single deep question (no intervening user prompt)."""
    payload = json.dumps(
        {
            "total_matches": 1,
            "matches": [{"citation_path": citation, "score": 0.9}],
        }
    )
    return [
        _assistant_tool_use(
            tool_id=f"tu_{idx}",
            tool_name="search_bylaw_evidence",
            tool_input={"query": f"q{idx}"},
        ),
        _tool_result(f"tu_{idx}", payload),
    ]


def _in_flight_loop(rounds: int) -> list[Message]:
    """A conversation as the loop sees it at the top of an iteration:
    one user question followed by ``rounds`` completed tool_result
    turns, ending on the most recent tool_result."""
    out: list[Message] = [_user_text("deep question")]
    for i in range(1, rounds + 1):
        out.extend(_search_round(i, citation=f"{i}.0"))
    return out


# -- resolve_tool_loop_keep_recent ----------------------------------------


def test_resolve_tool_loop_keep_recent_explicit_field_wins(monkeypatch):
    monkeypatch.setenv("ADVISOR_TOOL_LOOP_COMPACT_KEEP_RECENT", "9")
    assert resolve_tool_loop_keep_recent(4) == 4


def test_resolve_tool_loop_keep_recent_env_used_when_field_none(monkeypatch):
    monkeypatch.setenv("ADVISOR_TOOL_LOOP_COMPACT_KEEP_RECENT", "5")
    assert resolve_tool_loop_keep_recent(None) == 5


def test_resolve_tool_loop_keep_recent_defaults_to_three(monkeypatch):
    monkeypatch.delenv("ADVISOR_TOOL_LOOP_COMPACT_KEEP_RECENT", raising=False)
    assert resolve_tool_loop_keep_recent(None) == 3


def test_resolve_tool_loop_keep_recent_invalid_env_falls_back(monkeypatch):
    monkeypatch.setenv("ADVISOR_TOOL_LOOP_COMPACT_KEEP_RECENT", "nope")
    assert resolve_tool_loop_keep_recent(None) == 3


def test_resolve_tool_loop_keep_recent_zero_is_disable_switch(monkeypatch):
    """Unlike the submission-path resolver (which clamps to 1), a
    non-positive value here is the explicit ops kill-switch — it
    disables in-loop compaction rather than clamping."""
    monkeypatch.setenv("ADVISOR_TOOL_LOOP_COMPACT_KEEP_RECENT", "0")
    assert resolve_tool_loop_keep_recent(None) == 0
    assert resolve_tool_loop_keep_recent(0) == 0
    assert resolve_tool_loop_keep_recent(-2) == -2


# -- compaction behaviour --------------------------------------------------


def test_tool_loop_no_compaction_within_keep_window():
    """Three completed rounds with keep_recent_results=3 leaves every
    tool_result full — nothing has fallen out of the window yet."""
    messages = _in_flight_loop(3)
    out = compact_tool_loop_history(messages, keep_recent_results=3)
    assert out == messages


def test_tool_loop_compacts_oldest_round_when_window_exceeded():
    """Four rounds, keep last 2: rounds 1-2 summarise, rounds 3-4 stay
    full. The cutoff is the third tool_result turn."""
    messages = _in_flight_loop(4)
    out = compact_tool_loop_history(messages, keep_recent_results=2)

    # Round 1 tool_result (index 2) and round 2 (index 4) summarised.
    for tr_index, citation in ((2, "1.0"), (4, "2.0")):
        block = out[tr_index].content[0]
        assert isinstance(block, ToolResultBlock)
        assert block.content.startswith("[retrieved: 1 match for 'q")
        assert f"citations {citation}" in block.content

    # Rounds 3-4 (indices 6, 8) stay byte-for-byte intact.
    for tr_index in (6, 8):
        assert out[tr_index] is messages[tr_index]

    # tool_use blocks always pass through.
    assert isinstance(out[1].content[-1], ToolUseBlock)


def test_tool_loop_keeps_latest_round_full_for_breakpoint():
    """The most recent tool_result turn must stay full so the rolling
    cache breakpoint (ABS-285) rides an uncompacted turn."""
    messages = _in_flight_loop(5)
    out = compact_tool_loop_history(messages, keep_recent_results=1)
    last_tr = out[-1].content[0]
    assert isinstance(last_tr, ToolResultBlock)
    # Full JSON payload, not a summary.
    assert last_tr.content == messages[-1].content[0].content
    assert last_tr.content.startswith("{")


def test_tool_loop_disabled_when_keep_recent_non_positive():
    messages = _in_flight_loop(5)
    out = compact_tool_loop_history(messages, keep_recent_results=0)
    assert out == messages
    out_neg = compact_tool_loop_history(messages, keep_recent_results=-1)
    assert out_neg == messages


def test_tool_loop_compaction_does_not_mutate_input():
    messages = _in_flight_loop(5)
    snapshot = json.dumps(
        [m.model_dump(mode="json") for m in messages], sort_keys=True
    )
    compact_tool_loop_history(messages, keep_recent_results=2)
    after = json.dumps(
        [m.model_dump(mode="json") for m in messages], sort_keys=True
    )
    assert snapshot == after


def test_tool_loop_compaction_is_deterministic_and_prefix_stable():
    """The load-bearing cache property: as the loop grows by one round,
    every already-compacted turn keeps byte-identical bytes, so the
    cached prefix up to the newest-still-full turn never shifts. Only
    the one turn that newly falls out of the window changes."""
    five = _in_flight_loop(5)
    six = _in_flight_loop(6)

    out5 = compact_tool_loop_history(five, keep_recent_results=2)
    out6 = compact_tool_loop_history(six, keep_recent_results=2)

    def _tr_bytes(msgs: list[Message]) -> list[str]:
        return [
            m.content[0].content
            for m in msgs
            if isinstance(m.content, list)
            and isinstance(m.content[0], ToolResultBlock)
        ]

    five_tr = _tr_bytes(out5)
    six_tr = _tr_bytes(out6)

    # Iteration N=5 compacted rounds 1-3 (full: 4,5). Iteration N=6
    # compacts rounds 1-4 (full: 5,6). Rounds 1-3 are identical across
    # both iterations — the stable, already-cached prefix.
    assert six_tr[:3] == five_tr[:3]
    # Round 4 transitioned full -> summary at N=6 (the one churned turn).
    assert five_tr[3].startswith("{")  # was full at N=5
    assert six_tr[3].startswith("[retrieved:")  # summarised at N=6
