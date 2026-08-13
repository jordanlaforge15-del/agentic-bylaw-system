"""Compact-projection coverage for ABS-409 zone-uses additions.

The advisor LLM reads ``compact_zone_profile``'s output, not the raw DTO —
so the new ``conditional`` bucket, the list caps, and the path-less-citation
fallback must all survive projection.
"""
from __future__ import annotations

from advisor.chat.compact import _USE_LIST_CAP, compact_zone_profile
from bylaw_retrieval.retrieval.schemas import (
    CitationRef,
    ConditionalUse,
    ZoneProfile,
    ZoneUses,
)


def _profile(**kwargs) -> ZoneProfile:
    base = dict(zone="COR", unknown_zone=False, citations=[])
    base.update(kwargs)
    return ZoneProfile(**base)


def test_conditional_uses_project_with_footnote_and_capped_condition():
    profile = _profile(
        uses=ZoneUses(
            permitted=["Office use"],
            conditional=[
                ConditionalUse(use="Restaurant use", footnote_ordinal=3, condition="x" * 500)
            ],
        )
    )
    out = compact_zone_profile(profile)
    assert out["uses"]["permitted"] == ["Office use"]
    conditional = out["uses"]["conditional"]
    assert conditional[0]["use"] == "Restaurant use"
    assert conditional[0]["footnote"] == 3
    assert len(conditional[0]["condition"]) <= 160


def test_long_use_lists_are_capped_with_more_marker():
    profile = _profile(uses=ZoneUses(permitted=[f"Use {i}" for i in range(90)]))
    out = compact_zone_profile(profile)
    permitted = out["uses"]["permitted"]
    assert len(permitted) == _USE_LIST_CAP + 1
    assert permitted[-1] == f"+{90 - _USE_LIST_CAP} more"


def test_pathless_table_citation_projects_label_and_pages():
    profile = _profile(
        uses=ZoneUses(permitted=["Office use"]),
        citations=[
            CitationRef(
                citation_path=None,
                citation_label="Table 1A: Permitted uses by zone",
                page_start=45,
                page_end=46,
                backs=["uses"],
            )
        ],
    )
    out = compact_zone_profile(profile)
    ref = out["citations"][0]
    assert "citation_path" not in ref
    assert ref["citation_label"].startswith("Table 1A")
    assert ref["pages"] == [45, 46]
    assert ref["backs"] == ["uses"]


def test_conditional_only_uses_still_project():
    profile = _profile(
        uses=ZoneUses(conditional=[ConditionalUse(use="Restaurant use")])
    )
    out = compact_zone_profile(profile)
    assert out["uses"]["conditional"][0] == {"use": "Restaurant use"}


# ---------------------------------------------------------------------------
# ABS-484 — the undetermined bucket has to reach the model, with the language
# that stops it being relayed as a prohibition
# ---------------------------------------------------------------------------


def test_undetermined_uses_project_with_a_do_not_conclude_instruction():
    profile = _profile(
        uses=ZoneUses(permitted=["Office use"], undetermined=["Restaurant use"])
    )
    out = compact_zone_profile(profile)
    assert out["uses"]["undetermined"] == ["Restaurant use"]
    instruction = out["uses"]["instruction"]
    assert "not determinable" in instruction
    assert "prohibited" in instruction


def test_undetermined_only_uses_still_project():
    """A zone whose whole column was unreadable used to project no ``uses`` key
    at all, which reads to the model as 'no use restrictions found'."""
    profile = _profile(uses=ZoneUses(undetermined=["Restaurant use"]))
    out = compact_zone_profile(profile)
    assert out["uses"]["undetermined"] == ["Restaurant use"]
    assert "permitted" not in out["uses"]
    assert "not_permitted" not in out["uses"]
