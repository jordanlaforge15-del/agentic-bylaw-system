"""ABS-517: bounded capture of tool inputs and results.

The behaviour under test is the one that makes a failing eval case
diagnosable: given a transcript, can a reader tell whether a provision
came back from retrieval (synthesis dropped it) or never came back
(retrieval missed it)? Every test here is a facet of that question, plus
the size bounds that keep the answer shippable on every turn.
"""
from __future__ import annotations

import json

import pytest

from advisor.chat.tool_payloads import (
    bound_tool_input,
    extract_result_citations,
    input_value_limit,
    render_tool_result,
    result_excerpt_limit,
)
from advisor.chat.session import _build_tool_call_metric
from advisor.llm import TextBlock
from advisor.llm.tool_loop import ToolInvocation


def _search_result(*labels: str, text_len: int = 2000) -> str:
    """A compact_search_response-shaped payload naming the given citations."""
    return json.dumps(
        {
            "total_matches": len(labels),
            "shown_matches": len(labels),
            "matches": [
                {
                    "fragment_id": i,
                    "citation_label": label,
                    "citation_path": f"part8/{label.replace(' ', '')}",
                    "text": "x" * text_len,
                }
                for i, label in enumerate(labels)
            ],
        }
    )


def _invocation(**overrides) -> ToolInvocation:
    kwargs = {
        "tool_use_id": "toolu_1",
        "tool_name": "search_bylaw_evidence",
        "input": {"query": "side yard setback", "limit": 10},
        "output": _search_result("s. 198", "s. 199"),
    }
    kwargs.update(overrides)
    return ToolInvocation(**kwargs)


# ---------------------------------------------------------------------------
# Input capture
# ---------------------------------------------------------------------------


def test_input_is_captured_verbatim_when_short():
    assert bound_tool_input({"query": "rear setback", "limit": 5}) == {
        "query": "rear setback",
        "limit": 5,
    }


def test_no_arguments_is_distinguishable_from_no_recording():
    """``{}`` and ``None`` must not collapse — they mean different things."""
    assert bound_tool_input({}) == {}
    assert bound_tool_input(None) is None


def test_long_string_values_are_truncated_but_structure_survives():
    """A pasted submission body must not drag the whole body onto the stream.

    Structure is kept because the *shape* of the arguments is diagnostic:
    whether ``citation_path_prefix`` was passed at all is the difference
    between a broad search and a scoped one.
    """
    payload = {
        "narrative": "y" * 5000,
        "scope": {"citation_path_prefix": "part8", "tags": ["a", "z" * 5000]},
    }

    bounded = bound_tool_input(payload)

    limit = input_value_limit()
    assert len(bounded["narrative"]) < 5000
    assert bounded["narrative"].startswith("y" * limit)
    assert bounded["scope"]["citation_path_prefix"] == "part8"
    assert bounded["scope"]["tags"][0] == "a"
    assert len(bounded["scope"]["tags"][1]) < 5000


def test_input_value_limit_is_env_tunable(monkeypatch):
    monkeypatch.setenv("ADVISOR_TOOL_INPUT_VALUE_CHARS", "10")
    bounded = bound_tool_input({"query": "a" * 50})
    assert bounded["query"].startswith("a" * 10)
    assert len(bounded["query"]) < 50


@pytest.mark.parametrize("junk", ["", "not-a-number", "-5"])
def test_junk_env_values_fall_back_to_the_default(junk, monkeypatch):
    """An ops typo must not silently disable capture."""
    monkeypatch.setenv("ADVISOR_TOOL_RESULT_EXCERPT_CHARS", junk)
    assert result_excerpt_limit() > 0


# ---------------------------------------------------------------------------
# Result capture
# ---------------------------------------------------------------------------


def test_short_result_is_captured_whole_and_marked_untruncated():
    excerpt, chars, truncated = render_tool_result('{"total_matches": 0}')
    assert excerpt == '{"total_matches": 0}'
    assert chars == len('{"total_matches": 0}')
    assert truncated is False


def test_large_result_is_bounded_and_reports_what_it_hid():
    output = _search_result("s. 198", "s. 199", text_len=20_000)

    excerpt, chars, truncated = render_tool_result(output)

    assert truncated is True
    assert chars == len(output)
    # Bounded to the configured limit plus the truncation marker.
    assert len(excerpt) < result_excerpt_limit() + 40
    assert excerpt.startswith('{"total_matches": 2')


def test_a_failed_call_reports_its_error_as_the_excerpt():
    """Why a call produced nothing is as load-bearing as a payload."""
    excerpt, chars, truncated = render_tool_result(
        None, error="ValueError: unknown citation path"
    )
    assert excerpt == "ValueError: unknown citation path"
    assert chars == len("ValueError: unknown citation path")
    assert truncated is False


def test_content_block_output_is_flattened_to_text():
    excerpt, _, _ = render_tool_result([TextBlock(text="a"), TextBlock(text="b")])
    assert excerpt == "a\nb"


def test_absent_output_records_nothing_rather_than_an_empty_string():
    assert render_tool_result(None) == (None, None, False)


def test_result_capture_can_be_switched_off_but_length_still_ships(monkeypatch):
    """``0`` gives an operator the leaner stream back without losing the count.

    The length distinguishes "capture disabled" from "the tool returned
    nothing", which are very different readings of the same null excerpt.
    """
    monkeypatch.setenv("ADVISOR_TOOL_RESULT_EXCERPT_CHARS", "0")

    excerpt, chars, truncated = render_tool_result(_search_result("s. 198"))

    assert excerpt is None
    assert chars > 0
    assert truncated is False


# ---------------------------------------------------------------------------
# The citation index — what makes the bound safe
# ---------------------------------------------------------------------------


def test_citations_are_indexed_from_the_whole_result_not_just_the_excerpt():
    """The point of the index: head truncation must not hide a low-ranked hit.

    s.333(1)(a) sits behind 20 matches of 5 KB each, far past any sane
    excerpt bound. Without the index a reader would conclude it was never
    retrieved and go fix retrieval — the wrong layer.
    """
    labels = [f"s. {200 + i}" for i in range(20)] + ["s. 333(1)(a)"]
    output = _search_result(*labels, text_len=5000)

    excerpt, _, truncated = render_tool_result(output)
    citations = extract_result_citations(output)

    assert truncated is True
    assert "s. 333(1)(a)" not in excerpt
    assert "s. 333(1)(a)" in citations


def test_citation_order_preserves_retrieval_rank():
    """"Returned but ranked last" is a different diagnosis from "returned first"."""
    citations = extract_result_citations(_search_result("Table 1B", "s. 198"))
    assert citations.index("Table 1B") < citations.index("s. 198")


def test_citations_are_deduplicated_on_first_sighting():
    output = json.dumps(
        {
            "matches": [
                {"citation_label": "s. 198"},
                {"citation_label": "s. 198"},
                {"citation_label": "s. 199"},
            ]
        }
    )
    assert extract_result_citations(output) == ["s. 198", "s. 199"]


def test_the_citation_index_is_capped_and_says_when_it_clipped():
    output = _search_result(*[f"s. {i}" for i in range(200)], text_len=10)

    citations = extract_result_citations(output)

    assert len(citations) <= 81  # the cap plus its overflow marker
    assert citations[-1].startswith("+more")


@pytest.mark.parametrize(
    "output", ["plain prose, not JSON", "", None, "{malformed", "[1, 2, 3]"]
)
def test_non_json_results_index_to_nothing_rather_than_raising(output):
    """Best-effort index, not a parser contract — a 20-case run must not die."""
    assert extract_result_citations(output) == []


# ---------------------------------------------------------------------------
# The metric the session actually emits
# ---------------------------------------------------------------------------


def test_metric_carries_input_excerpt_and_citations_together():
    metric = _build_tool_call_metric(_invocation())

    assert metric.name == "search_bylaw_evidence"
    assert metric.is_error is False
    assert metric.input == {"query": "side yard setback", "limit": 10}
    assert metric.result_excerpt
    assert metric.result_chars > 0
    assert "s. 198" in metric.result_citations


def test_metric_for_a_failed_call_flags_the_error_and_keeps_its_text():
    metric = _build_tool_call_metric(
        _invocation(output=None, error="KeyError: 'zone'", latency_ms=12)
    )

    assert metric.is_error is True
    assert metric.result_excerpt == "KeyError: 'zone'"
    assert metric.result_citations == []
    assert metric.latency_ms == 12


def test_metric_serializes_to_the_shape_the_eval_runner_reads():
    """The SSE contract: model_dump is what ``_format_sse_event`` ships."""
    dumped = _build_tool_call_metric(_invocation()).model_dump(mode="json")

    assert set(dumped) >= {
        "name",
        "is_error",
        "latency_ms",
        "input",
        "result_excerpt",
        "result_chars",
        "result_truncated",
        "result_citations",
    }
    assert json.loads(json.dumps(dumped))  # JSON-serializable end to end
