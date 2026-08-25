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


# ---------------------------------------------------------------------------
# ABS-524 — the permission table travels with the permission it grants
# ---------------------------------------------------------------------------
#
# The failing TC-022 runs had Table 1B in context the whole time: the profile's
# citations tail carried it with a path, a label and pages, keyed to the uses
# block by ``backs: ["uses"]``. The answer stated the permission and never
# named it. Two things were missing at the point of use — the quotable label
# (suppressed whenever a path was present) and any statement that the fact and
# the citation belong together.


def _table_1b(**overrides) -> CitationRef:
    base = dict(
        citation_path="Part I > [Table 1B]",
        citation_label="Table 1B",
        page_start=48,
        page_end=48,
        backs=["uses"],
    )
    base.update(overrides)
    return CitationRef(**base)


def test_path_bearing_table_citation_keeps_its_label_and_pages():
    """The label is what the answer prints; the path is what lookup_citation
    takes. Emitting only the path made the model derive "Table 1B" by parsing
    "Part I > [Table 1B]" — a step it was observed to skip."""
    profile = _profile(uses=ZoneUses(permitted=["Townhouse dwelling use"]),
                       citations=[_table_1b()])
    ref = compact_zone_profile(profile)["citations"][0]

    assert ref["citation_path"] == "Part I > [Table 1B]"
    assert ref["citation_label"] == "Table 1B"
    assert ref["pages"] == [48, 48]
    assert ref["backs"] == ["uses"]


def test_uses_block_carries_the_table_that_grants_it():
    profile = _profile(
        uses=ZoneUses(permitted=["Townhouse dwelling use"]),
        citations=[_table_1b()],
    )
    uses = compact_zone_profile(profile)["uses"]

    assert uses["cite_as"] == [
        {
            "citation_path": "Part I > [Table 1B]",
            "citation_label": "Table 1B",
            "pages": [48, 48],
        }
    ]
    instruction = uses["citation_instruction"]
    assert "citation_label" in instruction
    assert "heading" in instruction


def test_only_uses_backing_citations_are_bound_to_the_uses_block():
    """Section 233 backs the unit-count cap, not the permission. Binding it to
    the uses block would invite exactly the substitution ABS-524 is about —
    citing the cap and calling the permission cited."""
    profile = _profile(
        uses=ZoneUses(permitted=["Townhouse dwelling use"]),
        citations=[
            _table_1b(),
            CitationRef(
                citation_path="Part V > 233 > (b)",
                citation_label="233",
                backs=["max_units"],
            ),
        ],
    )
    uses = compact_zone_profile(profile)["uses"]

    assert [c["citation_label"] for c in uses["cite_as"]] == ["Table 1B"]


def test_uses_block_without_a_citation_makes_no_attribution_claim():
    """ABS-484's all-holes column cites nothing for uses on purpose. An empty
    ``cite_as`` — or an instruction to cite a source that isn't there — would
    push the model to invent one."""
    profile = _profile(uses=ZoneUses(undetermined=["Restaurant use"]), citations=[])
    uses = compact_zone_profile(profile)["uses"]

    assert "cite_as" not in uses
    assert "citation_instruction" not in uses
