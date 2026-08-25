"""Compact-projection coverage for ABS-409 zone-uses additions.

The advisor LLM reads ``compact_zone_profile``'s output, not the raw DTO —
so the new ``conditional`` bucket, the list caps, and the path-less-citation
fallback must all survive projection.
"""
from __future__ import annotations

from advisor.chat.compact import (
    _CONDITION_TEXT_CAP,
    _USE_LIST_CAP,
    compact_zone_profile,
)
from bylaw_retrieval.retrieval.schemas import (
    CitationRef,
    ConditionalUse,
    FootnoteCondition,
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
                ConditionalUse(
                    use="Restaurant use",
                    footnotes=[FootnoteCondition(ordinal=3, text="x" * 500)],
                )
            ],
        )
    )
    out = compact_zone_profile(profile)
    assert out["uses"]["permitted"] == ["Office use"]
    conditional = out["uses"]["conditional"]
    assert conditional[0]["use"] == "Restaurant use"
    condition = conditional[0]["conditions"][0]
    assert condition["footnote"] == 3
    assert len(condition["text"]) == _CONDITION_TEXT_CAP + 1  # + the marker
    # ABS-523: shortened, and *said* to be shortened. A legend cut at the cap
    # reads as a legend that ended there, and the model answers from it.
    assert condition["text"].endswith("…")
    assert condition["text_truncated"] is True


def test_a_condition_within_the_cap_is_not_marked_as_shortened():
    """The marker has to mean something. If it rode along on every condition
    the model would treat every legend as partial and drill down on all of
    them — the cost the cap exists to avoid."""
    profile = _profile(
        uses=ZoneUses(
            conditional=[
                ConditionalUse(
                    use="Restaurant use",
                    footnotes=[FootnoteCondition(ordinal=3, text="y" * 40)],
                )
            ],
        )
    )
    condition = compact_zone_profile(profile)["uses"]["conditional"][0]["conditions"][0]
    assert condition["text"] == "y" * 40
    assert "text_truncated" not in condition


def test_every_footnote_on_a_conditional_cell_survives_projection():
    """ABS-523: a cell reading '⑮ ㉒' reaches the model as two conditions.

    ``get_zone_profile`` is the case-open shortcut the agent calls first, so a
    condition dropped here is the agent's first impression of the zone. On
    TC-023 the survivor was ⑮ — a Halifax Grain Elevator carve-out irrelevant
    to the address — and the dropped ㉒ was the footnote authorising more than
    8 units in ER-3.

    ㉒ is quoted at its real corpus length (281 characters) on purpose. An
    abridged legend fits inside any plausible cap, so a test written against
    one grades the cap as harmless no matter where it sits — which is how the
    first pass at this shipped a 160-character cap that severed ㉒ one clause
    before it named the routes.
    """
    profile = _profile(
        zone="ER-3",
        uses=ZoneUses(
            conditional=[
                ConditionalUse(
                    use="Multi-unit dwelling use",
                    footnotes=[
                        FootnoteCondition(
                            ordinal=15,
                            text="⑮ Use is permitted, except within the Halifax "
                            "Grain Elevator (HGE) Special Area.",
                        ),
                        FootnoteCondition(
                            ordinal=22,
                            text="㉒ A multi-unit dwelling use that contains up to "
                            "8 dwelling units is permitted in the ER-3 zone, in "
                            "accordance with Section 231.3, and a multi-unit "
                            "dwelling use that contains more than 8 units is "
                            "permitted in the ER-3 zone in accordance with "
                            "Section 63 or Subsection 233(3).",
                        ),
                    ],
                )
            ],
        ),
    )
    out = compact_zone_profile(profile)
    conditions = out["uses"]["conditional"][0]["conditions"]
    assert [c["footnote"] for c in conditions] == [15, 22]
    # The operative half of the legend is the second half: the cap has to clear
    # the whole sentence, not the part that states the restriction.
    assert "Section 63" in conditions[1]["text"]
    assert "233(3)" in conditions[1]["text"]
    assert "text_truncated" not in conditions[1]


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
    base = {
        "citation_path": "Part I > [Table 1B]",
        "citation_label": "Table 1B",
        "page_start": 48,
        "page_end": 48,
        "backs": ["uses"],
    }
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
