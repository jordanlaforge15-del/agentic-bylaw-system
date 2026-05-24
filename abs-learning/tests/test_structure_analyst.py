"""Tests for the Phase 1 Structure Analyst agent.

These tests mock the Anthropic client so no real API traffic occurs.
"""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from agents.structure_analyst import StructureAnalystAgent


def make_response(payload: dict):
    """Build a fake Anthropic response carrying a single tool_use block."""
    block = MagicMock()
    block.type = "tool_use"
    block.name = "report_citation_hierarchy"
    block.input = payload
    response = MagicMock()
    response.content = [block]
    return response


# --------------------------------------------------------------------------- T3-A
def test_extract_patterns_from_window_returns_valid_schema():
    payload = {
        "hierarchy": [
            {"level": "part", "pattern": r"^Part [IVX]+", "label_format": "Part {roman}"},
            {"level": "section", "pattern": r"^\d+\.", "label_format": "{n}."},
            {"level": "subsection", "pattern": r"^\(\d+\)", "label_format": "({n})"},
            {"level": "clause", "pattern": r"^\([a-z]\)", "label_format": "({a})"},
        ],
        "schedule_patterns": [r"^Schedule [A-Z]"],
        "table_caption_pattern": r"^Table \d+",
        "citation_example": "Part I > 1. > (1) > (a)",
        "separator": " > ",
        "flags": [],
        "confidence": 0.92,
    }
    fake_client = MagicMock()
    fake_client.messages.create.return_value = make_response(payload)

    agent = StructureAnalystAgent(fake_client)
    result = agent._extract_patterns_from_window(
        window={
            "text": "Part I — General\n1. Title\n(1) These are the rules\n(a) first item",
            "window_index": 0,
            "start_page": 100,
            "end_page": 124,
        }
    )

    expected_keys = {
        "hierarchy",
        "schedule_patterns",
        "table_caption_pattern",
        "citation_example",
        "separator",
        "flags",
    }
    assert expected_keys.issubset(result.keys())
    assert len(result["hierarchy"]) >= 3
    for entry in result["hierarchy"]:
        assert "level" in entry
        assert "pattern" in entry
        assert "label_format" in entry

    # Confirm we actually went through the Anthropic SDK with a forced tool call.
    fake_client.messages.create.assert_called_once()
    call_kwargs = fake_client.messages.create.call_args.kwargs
    assert call_kwargs["tool_choice"] == {
        "type": "tool",
        "name": "report_citation_hierarchy",
    }
    assert call_kwargs["tools"][0]["name"] == "report_citation_hierarchy"


def test_extract_patterns_raises_when_no_tool_use_block():
    """Free-text responses must be rejected with ValueError."""
    text_block = MagicMock()
    text_block.type = "text"
    response = MagicMock()
    response.content = [text_block]

    fake_client = MagicMock()
    fake_client.messages.create.return_value = response

    agent = StructureAnalystAgent(fake_client)
    with pytest.raises(ValueError):
        agent._extract_patterns_from_window(
            window={"text": "anything", "window_index": 0, "start_page": 1, "end_page": 2}
        )


# --------------------------------------------------------------------------- T3-B
def _three_window_results():
    common_schedule = [r"^Schedule [A-Z]"]
    return [
        {
            "hierarchy": [
                {"level": "section", "pattern": r"^\d+", "label_format": "{n}"},
                {"level": "clause", "pattern": r"^\([a-z]\)", "label_format": "({a})"},
            ],
            "schedule_patterns": list(common_schedule),
            "table_caption_pattern": None,
            "citation_example": "1 > (a)",
            "separator": " > ",
            "flags": [],
            "confidence": 0.9,
        },
        {
            "hierarchy": [
                {"level": "section", "pattern": r"^\d+", "label_format": "{n}"},
                {"level": "clause", "pattern": r"^\([a-z]\)", "label_format": "({a})"},
            ],
            "schedule_patterns": list(common_schedule),
            "table_caption_pattern": None,
            "citation_example": "1 > (a)",
            "separator": " > ",
            "flags": [],
            "confidence": 0.9,
        },
        {
            "hierarchy": [
                {"level": "section", "pattern": r"^\d+\.", "label_format": "{n}."},
                {"level": "clause", "pattern": r"^\([a-z]\)", "label_format": "({a})"},
            ],
            "schedule_patterns": list(common_schedule),
            "table_caption_pattern": None,
            "citation_example": "1 > (a)",
            "separator": " > ",
            "flags": [],
            "confidence": 0.9,
        },
    ]


def test_merge_patterns_majority_wins_and_records_conflict():
    fake_client = MagicMock()
    agent = StructureAnalystAgent(fake_client)

    merged = agent._merge_patterns(
        _three_window_results(), jurisdiction_code="halifax"
    )

    section_entry = next(
        h for h in merged.citation_scheme.hierarchy if h.level == "section"
    )
    assert section_entry.pattern == r"^\d+"

    conflict_flags = [
        f
        for f in merged.flags
        if "section" in f.lower() and "conflict" in f.lower()
    ]
    assert conflict_flags, f"expected a section conflict flag, got {merged.flags!r}"

    # parser_version should reflect the supplied jurisdiction code.
    assert merged.parser_version == "docling:halifax"


# --------------------------------------------------------------------------- T3-C
def test_merge_patterns_confidence_reduced_by_conflicts():
    fake_client = MagicMock()
    agent = StructureAnalystAgent(fake_client)

    merged = agent._merge_patterns(
        _three_window_results(), jurisdiction_code="halifax"
    )

    assert merged.confidence < 0.9
    assert abs(merged.confidence - 0.85) < 1e-6


# --------------------------------------------------------------------- ABS-94
def test_abs94_extract_patterns_sends_cache_control_on_system_and_tool():
    """StructureAnalyst makes ~4 calls per bylaw with identical system
    prompt + tool schema. Verify cache_control is on the request payload."""
    payload = {
        "hierarchy": [],
        "schedule_patterns": [],
        "table_caption_pattern": None,
        "citation_example": "",
        "separator": " > ",
        "flags": [],
        "confidence": 0.0,
    }
    fake_client = MagicMock()
    fake_client.messages.create.return_value = make_response(payload)
    agent = StructureAnalystAgent(fake_client)
    agent._extract_patterns_from_window(
        window={"text": "stub", "window_index": 0,
                "start_page": 1, "end_page": 25}
    )
    kwargs = fake_client.messages.create.call_args.kwargs
    assert isinstance(kwargs["system"], list)
    assert kwargs["system"][0]["cache_control"] == {"type": "ephemeral"}
    assert kwargs["tools"][0]["cache_control"] == {"type": "ephemeral"}
    # Tool name preserved (not clobbered by spread + add).
    assert kwargs["tools"][0]["name"] == "report_citation_hierarchy"
