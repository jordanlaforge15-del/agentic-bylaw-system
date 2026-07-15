"""ABS-342: mapping the engine's markdown answer into the typed report.

``build_report`` is the server side of the ReportDocument contract — it
turns a captured purchase's markdown ``answer_text`` into the structured
``ReportContent`` (verdict + summary + typed block array + lifted
letterhead/footer meta) the client renders. These tests pin the parser's
block classification and the envelope's derived fields.
"""
from __future__ import annotations

from datetime import datetime, timezone

from advisor.billing import report as report_mod
from advisor.billing.report import build_report, parse_blocks
from advisor.db.models import QuestionPurchase, User


def _purchase(**kw) -> QuestionPurchase:
    defaults = dict(
        id=321,
        user_id=1,
        question_slug="due_diligence",
        inputs_json={"address": "1234 Elm Street"},
        price_cents=19_900,
        currency="CAD",
        status="captured",
        answer_text="An answer.",
        settled_at=datetime(2026, 7, 3, 12, 0, tzinfo=timezone.utc),
        metadata_json={},
    )
    defaults.update(kw)
    p = QuestionPurchase(**defaults)
    p.user = User(id=1, clerk_user_id="u1", email="buyer@example.com", full_name="Jordan Buyer")
    return p


# -- Block parsing ----------------------------------------------------------


def test_parse_summary_is_the_lead_paragraph() -> None:
    md = "This is the lead summary.\n\n## Details\nBody text."
    summary, blocks = parse_blocks(md)
    assert summary == "This is the lead summary."
    assert blocks and blocks[0]["type"] == "prose"
    assert blocks[0]["title"] == "Details"


def test_parse_keyvals_block() -> None:
    md = (
        "Lead.\n\n## Zoning\n"
        "- **Zone:** ER-1\n"
        "- **Overlay:** Heritage\n"
    )
    _, blocks = parse_blocks(md)
    kv = [b for b in blocks if b["type"] == "keyvals"]
    assert kv, blocks
    assert {"k": "Zone", "v": "ER-1"} in kv[0]["items"]
    assert {"k": "Overlay", "v": "Heritage"} in kv[0]["items"]


def test_parse_uses_block_with_status_labels() -> None:
    md = (
        "Lead.\n\n## Permitted Uses\n"
        "- Single detached dwelling — permitted\n"
        "- Corner store — conditional\n"
        "- Industrial — prohibited\n"
    )
    _, blocks = parse_blocks(md)
    uses = [b for b in blocks if b["type"] == "uses"]
    assert uses, blocks
    statuses = {i["status"] for i in uses[0]["items"]}
    assert statuses == {"permitted", "conditional", "prohibited"}


def test_parse_flags_block_from_flag_heading() -> None:
    md = (
        "Lead.\n\n## Non-conformity Flags\n"
        "- Rear setback below the minimum\n"
        "- Lot coverage exceeds maximum\n"
    )
    _, blocks = parse_blocks(md)
    flags = [b for b in blocks if b["type"] == "flags"]
    assert flags, blocks
    assert len(flags[0]["items"]) == 2


def test_parse_table_block_with_per_row_status() -> None:
    md = (
        "Lead.\n\n## Development Standards\n"
        "| Standard | Required | Proposed | Result |\n"
        "| --- | --- | --- | --- |\n"
        "| Height | 11 m max | 10.5 m | PASS |\n"
        "| Front setback | 6 m min | 7.5 m | EXCEEDS |\n"
    )
    _, blocks = parse_blocks(md)
    tables = [b for b in blocks if b["type"] == "table"]
    assert tables, blocks
    table = tables[0]
    assert table["columns"][0] == "Standard"
    assert table["rows"][0]["status"] == "pass"
    assert table["rows"][1]["status"] == "exceeds"


def test_property_summary_table_carries_no_row_status() -> None:
    """ABS-366: a plain field/value table has no evaluation column, so a
    row like "Governing By-law" must never pick up a stray PASS/FAIL pill
    (the row label "Governing" contains the substring "over", which used
    to false-positive against the FAIL signal list)."""
    md = (
        "Lead.\n\n## Property Summary\n"
        "| Field | Value |\n"
        "| --- | --- |\n"
        "| Zone | ER-1 |\n"
        "| Governing By-law | Regional Centre Land Use By-law |\n"
    )
    _, blocks = parse_blocks(md)
    tables = [b for b in blocks if b["type"] == "table"]
    assert tables, blocks
    table = tables[0]
    assert all(row["status"] is None for row in table["rows"])


def test_na_verdict_rows_render_no_status_pill() -> None:
    """ABS-373: a row whose verdict is N/A / not-regulated / uncertain must
    carry no status, even inside an evaluation table. The Lot Coverage row
    regressed because "Lot Coverage" contains the substring "over" (a FAIL
    signal); the FAR row with the same "N/A (no limit)" verdict correctly had
    no pill, so the derivation was inconsistent. PASS/FAIL/EXCEEDS verdicts
    still produce their pills."""
    md = (
        "Lead.\n\n## Built-form standards\n"
        "| Standard | Required | Proposed | Result |\n"
        "| --- | --- | --- | --- |\n"
        "| Lot Coverage | — | 42% | N/A — no maximum in INS zone |\n"
        "| FAR | — | 1.4 | N/A (no limit) |\n"
        "| Density | not regulated | 30 u/ha | not regulated |\n"
        "| Parking | uncertain | 12 | UNCERTAIN |\n"
        "| Height | 11 m max | 10.5 m | PASS |\n"
        "| Front setback | 6 m min | 7.5 m | EXCEEDS |\n"
        "| Rear setback | 8 m min | 4 m | FAIL |\n"
    )
    _, blocks = parse_blocks(md)
    tables = [b for b in blocks if b["type"] == "table"]
    assert tables, blocks
    rows = tables[0]["rows"]
    assert rows[0]["status"] is None, "Lot Coverage N/A row must have no pill"
    assert rows[1]["status"] is None, "FAR N/A row must have no pill"
    assert rows[2]["status"] is None, "not-regulated row must have no pill"
    assert rows[3]["status"] is None, "UNCERTAIN row must have no pill"
    assert rows[4]["status"] == "pass"
    assert rows[5]["status"] == "exceeds"
    assert rows[6]["status"] == "fail"


def test_row_status_helper_neutral_verdicts() -> None:
    """ABS-373: unit-level guard on the pill-derivation helper itself."""
    assert report_mod._row_status(["Lot Coverage", "42%", "N/A — no maximum"]) is None
    assert report_mod._row_status(["FAR", "1.4", "N/A (no limit)"]) is None
    assert report_mod._row_status(["Density", "not regulated"]) is None
    assert report_mod._row_status(["Parking", "UNCERTAIN"]) is None
    assert report_mod._row_status(["Height", "10.5 m", "PASS"]) == "pass"
    assert report_mod._row_status(["Setback", "7.5 m", "EXCEEDS"]) == "exceeds"
    assert report_mod._row_status(["Rear yard", "4 m", "FAIL"]) == "fail"


def test_parse_finding_block_from_determination_heading() -> None:
    md = (
        "Lead.\n\n## Determination\n"
        "The existing structure sits within the minimum rear-yard setback and "
        "does not comply.\n"
    )
    _, blocks = parse_blocks(md)
    findings = [b for b in blocks if b["type"] == "finding"]
    assert findings, blocks
    assert findings[0]["status"] == "fail"
    assert findings[0]["title"] == "Determination"


def test_strips_agent_monologue_preamble_fenced_by_rule() -> None:
    """ABS-359: the engine fences its planning monologue off from the report
    with a leading horizontal rule. Neither the monologue nor the rule is
    report content — the summary must be the report's own lead."""
    md = (
        "I have gathered the key provisions. Now I have sufficient information "
        "to prepare the variance justification package. Let me compile the "
        "findings:\n\n---\n\n"
        "The requested rear-yard variance is supportable on all three tests.\n\n"
        "## Statutory Test 1\nThe variance is minor in nature.\n"
    )
    summary, blocks = parse_blocks(md)
    assert "compile the findings" not in summary.lower()
    assert "gathered the key provisions" not in summary.lower()
    assert "---" not in summary
    assert summary == "The requested rear-yard variance is supportable on all three tests."
    assert all("---" not in (b.get("text") or "") for b in blocks)


def test_strips_monologue_when_no_rule_fences_it() -> None:
    """The monologue is scrubbed even when the engine emits no separating
    rule — a lead that reads as chain-of-thought is never a summary."""
    md = "Let me compile the findings.\n\n## Determination\nThe use is permitted.\n"
    summary, _ = parse_blocks(md)
    assert summary == ""


def test_bare_address_preamble_before_rule_is_dropped() -> None:
    """ABS-359 (woodlawn repro): a bare address followed by a rule rendered
    as "address + ---" with no lead. The address (already in the letterhead)
    and the rule are scaffolding — the summary comes from the report body."""
    md = "1967 Woodlawn Terrace\n\n---\n\n## Zoning\n- **Zone:** ER-1\n"
    summary, blocks = parse_blocks(md)
    assert "1967 Woodlawn Terrace" not in summary
    assert "---" not in summary
    kv = [b for b in blocks if b["type"] == "keyvals"]
    assert kv, blocks


def test_promotes_title_section_intro_to_summary_when_no_lead() -> None:
    """When a report opens straight into its title heading, the intro
    paragraph of that first section becomes the clean lead summary."""
    md = (
        "---\n\n# Variance Justification Package\n\n"
        "This package supports the requested rear-yard variance.\n\n"
        "## Statutory Test 1\nThe variance is minor.\n"
    )
    summary, blocks = parse_blocks(md)
    assert summary == "This package supports the requested rear-yard variance."
    # The promoted intro is not also left behind as a duplicate block.
    assert all(
        b.get("text") != "This package supports the requested rear-yard variance."
        for b in blocks
    )


def test_strips_horizontal_rules_between_sections() -> None:
    """Decorative `---` dividers between sections must never leak into a
    prose block as literal text."""
    md = (
        "A clean lead.\n\n## Option 1\nHome office is permitted.\n\n"
        "---\n\n## Option 2\nHome occupation requires approval.\n"
    )
    summary, blocks = parse_blocks(md)
    assert summary == "A clean lead."
    assert all("---" not in (b.get("text") or "") for b in blocks)
    assert all("---" not in (b.get("body") or "") for b in blocks)


def test_heading_less_answer_degrades_to_prose() -> None:
    md = "Just a flat paragraph with no structure at all."
    summary, blocks = parse_blocks(md)
    assert summary == md
    # A pure-summary answer needs no duplicate prose block.
    assert all(b["type"] != "prose" or b["text"] != md for b in blocks)


def test_strips_based_on_my_research_preamble_without_fence() -> None:
    """ABS-359 re-test (PU-000011): an unfenced "Based on my research, I can
    now provide you with a complete answer:" lead was not in the old signal
    allowlist, so it leaked into the summary. The section intro is the lead."""
    md = (
        "Based on my research, I can now provide you with a complete answer:\n\n"
        "# Permitted Use Report\n\n"
        "The property at 5184 Morris St is in the DH-1 zone.\n\n"
        "## Conclusion\nResidential use is permitted.\n"
    )
    summary, blocks = parse_blocks(md)
    assert "based on my research" not in summary.lower()
    assert "complete answer" not in summary.lower()
    assert summary == "The property at 5184 Morris St is in the DH-1 zone."


def test_strips_trailing_signoff_from_finding_body() -> None:
    """ABS-359 re-test: the engine tacks a conversational sign-off ("I
    apologize that I cannot provide … Would you like me to research a different
    property?") onto its final section. It rides into the block body, not the
    lead, so lead-only stripping missed it."""
    md = (
        "# Variance Justification Package\n\n"
        "This package assesses the request against the three statutory tests.\n\n"
        "## Conclusion\n"
        "The variance is supportable.\n"
        "I apologize that I cannot provide a definitive citation-grounded "
        "answer for this specific query. Would you like me to research a "
        "different property or provision?\n"
    )
    summary, blocks = parse_blocks(md)
    finding = [b for b in blocks if b["type"] == "finding"]
    assert finding, blocks
    body = finding[0]["body"]
    assert "apologize" not in body.lower()
    assert "would you like" not in body.lower()
    assert body == "The variance is supportable."
    # Nothing in any block leaks the sign-off.
    assert "apologize" not in str(blocks).lower()


def test_scrub_keeps_real_prose_beside_signoff() -> None:
    """Scrubbing is sentence-level: a real sentence sitting on the same line as
    chatter survives while only the chatter is dropped."""
    md = (
        "A clean lead paragraph.\n\n"
        "## Setback\n"
        "The setback is 3 metres. Feel free to reach out with questions.\n"
    )
    _, blocks = parse_blocks(md)
    prose = [b for b in blocks if b["type"] == "prose"]
    assert prose, blocks
    assert prose[0]["text"] == "The setback is 3 metres."


def test_wholly_monologue_trailing_block_is_dropped() -> None:
    """A trailing section that is entirely a sign-off collapses to nothing —
    no empty block survives into the deliverable."""
    md = (
        "# Report\n\n"
        "The use is permitted as-of-right.\n\n"
        "## Next Steps\n"
        "I hope this helps. Would you like me to look at another property?\n"
    )
    _, blocks = parse_blocks(md)
    assert "hope this helps" not in str(blocks).lower()
    assert "would you like" not in str(blocks).lower()
    assert all(
        (b.get("text") or b.get("body") or "").strip() for b in blocks
    ), "an empty block leaked through"


# -- Envelope ---------------------------------------------------------------


def test_build_report_none_until_captured() -> None:
    assert build_report(_purchase(status="authorized", answer_text=None)) is None
    assert build_report(_purchase(status="voided", answer_text=None)) is None


def test_build_report_envelope_fields() -> None:
    p = _purchase(
        answer_text="A due-diligence summary.\n\n## Zoning\n- **Zone:** ER-1\n"
    )
    rep = build_report(p)
    assert rep is not None
    assert rep["ref"] == "DD-000321"
    assert rep["report_type"] == "Zoning due-diligence summary"
    assert rep["address"] == "1234 Elm Street"
    assert rep["issued"] == "2026-07-03"
    assert rep["prepared_for"] == "Jordan Buyer"
    assert rep["zone_subtitle"] == "ER-1"
    assert rep["bylaw_version"] == report_mod.BYLAW_VERSION
    assert rep["price_cents"] == 19_900
    assert "DD-000321" in rep["footer"]


def test_zone_subtitle_falls_back_to_default_when_body_has_no_zone() -> None:
    """ABS-362: the hardcoded literal is a last resort, not the norm — it
    only surfaces when the body genuinely carries no Zone row."""
    p = _purchase(answer_text="An answer with no zoning section at all.")
    rep = build_report(p)
    assert rep is not None
    assert rep["zone_subtitle"] == report_mod.DEFAULT_ZONE_SUBTITLE


def test_zone_subtitle_matches_body_zone_keyval() -> None:
    """ABS-362: the letterhead subtitle must never contradict the body's
    own Zone row — it derives from the same value, code-then-name."""
    p = _purchase(
        answer_text=(
            "A permitted-use check.\n\n## Property Summary\n"
            "- **Zone:** DH-1 (Downtown Halifax - 1)\n"
        )
    )
    rep = build_report(p)
    assert rep is not None
    assert rep["zone_subtitle"] == "DH-1 · Downtown Halifax - 1"
    zone_kv = next(
        item
        for block in rep["blocks"]
        if block["type"] == "keyvals"
        for item in block["items"]
        if item["k"] == "Zone"
    )
    assert zone_kv["v"] == "DH-1 (Downtown Halifax - 1)"


def test_zone_subtitle_matches_body_zone_table_row() -> None:
    """The Property Summary section often renders as a Field/Value table
    rather than bullets — the subtitle must derive from that shape too."""
    p = _purchase(
        answer_text=(
            "A due-diligence summary.\n\n## Property Summary\n"
            "| Field | Value |\n"
            "| --- | --- |\n"
            "| Zone | DH (Downtown Halifax) |\n"
        )
    )
    rep = build_report(p)
    assert rep is not None
    assert rep["zone_subtitle"] == "DH · Downtown Halifax"


def test_zone_subtitle_can_be_lifted_from_metadata() -> None:
    """Explicit metadata still wins over body-derivation when present."""
    p = _purchase(
        answer_text="A due-diligence summary.\n\n## Zoning\n- **Zone:** ER-1\n",
        metadata_json={"zone_subtitle": "Downtown Core"},
    )
    rep = build_report(p)
    assert rep is not None
    assert rep["zone_subtitle"] == "Downtown Core"


# -- Zone subtitle from the engine transcript (ABS-362 re-test) -------------
# The re-test proved the block-parse fix was insufficient: real answers state
# the zone free-form (often inline prose block parsing can't lift), so the
# letterhead fell back to the hardcoded default while the body clearly showed
# the resolved zone. The authoritative source is the engine's own spatial
# resolution — the grounding tool results in ``transcript_json``.


def _tool_use(block_id: str, name: str, tool_input: dict) -> dict:
    return {"type": "tool_use", "id": block_id, "name": name, "input": tool_input}


def _tool_result(block_id: str, payload: dict) -> dict:
    import json

    return {
        "type": "tool_result",
        "tool_use_id": block_id,
        "content": json.dumps(payload),
    }


def _transcript(*rounds) -> list:
    """Build a serialized tool loop: each round is (tool_use, tool_result)."""
    messages: list = []
    for use, result in rounds:
        messages.append({"role": "assistant", "content": [use]})
        messages.append({"role": "user", "content": [result]})
    return messages


def test_zone_subtitle_from_transcript_zone_profile() -> None:
    """The re-test scenario: the answer states the zone only in prose (which
    block parsing can't lift), but the letterhead still resolves it from the
    engine's ``get_zone_profile`` result rather than the hardcoded default."""
    p = _purchase(
        answer_text=(
            "The property at 5184 Morris St sits in the DH-1 (Downtown "
            "Halifax - 1) zone. The proposed use is permitted as-of-right."
        ),
        transcript_json=_transcript(
            (
                _tool_use("t1", "get_zone_profile", {"zone": "DH-1"}),
                _tool_result(
                    "t1",
                    {"zone": "DH-1", "zone_full_name": "Downtown Halifax - 1"},
                ),
            ),
        ),
    )
    rep = build_report(p)
    assert rep is not None
    assert rep["zone_subtitle"] == "DH-1 · Downtown Halifax - 1"
    assert rep["zone_subtitle"] != report_mod.DEFAULT_ZONE_SUBTITLE


def test_zone_subtitle_from_transcript_search_linked_datasets() -> None:
    """The zoning overlay on a ``search_bylaw_evidence`` hit is the same
    source the parcel pane reads — mine it when no zone profile ran."""
    p = _purchase(
        answer_text="A determination stated entirely in prose, no Zone row.",
        transcript_json=_transcript(
            (
                _tool_use(
                    "s1",
                    "search_bylaw_evidence",
                    {"query": "use", "location": {"civic_number": "5686", "street": "Spring Garden Rd"}},
                ),
                _tool_result(
                    "s1",
                    {
                        "matches": [
                            {
                                "linked_datasets": [
                                    {
                                        "name": "halifax_zoning_boundaries",
                                        "feature_matches": [
                                            {
                                                "canonical_attributes": {
                                                    "zone_code": "DH",
                                                    "zone_description": "Downtown Halifax",
                                                }
                                            }
                                        ],
                                    }
                                ]
                            }
                        ]
                    },
                ),
            ),
        ),
    )
    rep = build_report(p)
    assert rep is not None
    assert rep["zone_subtitle"] == "DH · Downtown Halifax"


def test_zone_subtitle_prefers_transcript_over_block_parse() -> None:
    """A code+name from the transcript wins over a bare code the block parser
    might otherwise surface, and always over the hardcoded default."""
    p = _purchase(
        answer_text="An answer with no zoning section at all.",
        transcript_json=_transcript(
            (
                _tool_use("a1", "get_address_profile", {"address": "5184 Morris St"}),
                _tool_result("a1", {"address": "5184 Morris St", "zone": "DH-1"}),
            ),
        ),
    )
    rep = build_report(p)
    assert rep is not None
    # Address-profile carries only the code, but it still beats the default.
    assert rep["zone_subtitle"] == "DH-1"


def test_zone_subtitle_from_transcript_tool_input_fallback() -> None:
    """When a compaction pass has summarised the zone-profile result body away,
    the ``get_zone_profile`` call *input* still preserves the code."""
    p = _purchase(
        answer_text="A determination stated entirely in prose, no Zone row.",
        transcript_json=[
            {
                "role": "assistant",
                "content": [
                    _tool_use("z1", "get_zone_profile", {"zone": "DH-2"}),
                ],
            },
            {
                "role": "user",
                "content": [
                    {
                        "type": "tool_result",
                        "tool_use_id": "z1",
                        "content": "[get_zone_profile: keys=zone,zone_full_name]",
                    }
                ],
            },
        ],
    )
    rep = build_report(p)
    assert rep is not None
    assert rep["zone_subtitle"] == "DH-2"


def test_zone_subtitle_metadata_wins_over_transcript() -> None:
    """An explicit metadata override still trumps the resolved transcript zone."""
    p = _purchase(
        answer_text="Prose only.",
        metadata_json={"zone_subtitle": "Downtown Core"},
        transcript_json=_transcript(
            (
                _tool_use("t1", "get_zone_profile", {"zone": "DH-1"}),
                _tool_result("t1", {"zone": "DH-1", "zone_full_name": "Downtown Halifax"}),
            ),
        ),
    )
    rep = build_report(p)
    assert rep is not None
    assert rep["zone_subtitle"] == "Downtown Core"


def test_prepared_for_falls_back_to_email() -> None:
    p = _purchase()
    p.user.full_name = None
    rep = build_report(p)
    assert rep is not None
    assert rep["prepared_for"] == "buyer@example.com"


def test_verdict_pass_vs_fail_vs_conditional() -> None:
    assert build_report(_purchase(answer_text="The use is permitted as-of-right."))[
        "verdict"
    ]["status"] == "pass"
    assert build_report(_purchase(answer_text="The proposal does not comply."))[
        "verdict"
    ]["status"] == "fail"
    assert build_report(
        _purchase(answer_text="Permitted, subject to a development permit.")
    )["verdict"]["status"] == "conditional"
    assert build_report(_purchase(answer_text="General background information."))[
        "verdict"
    ]["status"] == "attention"


def test_verdict_label_tailored_to_report_type() -> None:
    """ABS-365: the same content-derived status carries a label phrased in
    the report type's own frame — a variance package concludes on statutory-
    test supportability, not use-permission."""
    # A conditional variance package concludes on supportability, not
    # "Permitted with conditions".
    variance = build_report(
        _purchase(
            question_slug="variance_justification",
            answer_text="The variance is supportable, subject to conditions.",
        )
    )["verdict"]
    assert variance["status"] == "conditional"
    assert variance["label"] == "Supportable with conditions"

    # A clean-pass variance uses the design's three-statutory-tests wording.
    variance_pass = build_report(
        _purchase(
            question_slug="variance_justification",
            answer_text="The requested variance is permitted as-of-right on its merits.",
        )
    )["verdict"]
    assert variance_pass["status"] == "pass"
    assert variance_pass["label"] == "Supportable on all three statutory tests"

    # Development-standards concludes on compliance.
    standards = build_report(
        _purchase(
            question_slug="development_standards",
            answer_text="The proposal does not comply with the rear-yard setback.",
        )
    )["verdict"]
    assert standards["status"] == "fail"
    assert standards["label"] == "Does not comply"

    # Permitted-use keeps the use-permission frame.
    use = build_report(
        _purchase(
            question_slug="permitted_use",
            answer_text="The use is permitted as-of-right.",
        )
    )["verdict"]
    assert use["status"] == "pass"
    assert use["label"] == "Permitted as-of-right"


def test_variance_not_required_band() -> None:
    """ABS-377: a variance package that concludes the resolved requirement is
    already met renders the NOT REQUIRED band, not the supportability verdict —
    even when the answer also carries pass/fail keywords."""
    # The exact repro shape (VJ-000025 / the ABS-375 no-variance-required
    # answer): the answer says the setback "PASSES" AND "no variance is
    # required". The not-required conclusion must win over the pass signal so
    # the band never reads "PASS — Supportable on all three statutory tests".
    v = build_report(
        _purchase(
            question_slug="variance_justification",
            answer_text=(
                "The abutting parcel is zoned DH-1, so the required side yard "
                "is 0.0 m — the proposed 0.0 m side setback complies and PASSES, "
                "and no variance is required."
            ),
        )
    )["verdict"]
    assert v["status"] == "not_required"
    assert v["label"] == "Resolved requirement already met — no variance needed"

    # "variance may not be required" phrasing (the report's central finding)
    # also resolves to the not-required band, ahead of any fail keyword.
    v2 = build_report(
        _purchase(
            question_slug="variance_justification",
            answer_text=(
                "CRITICAL FINDING: variance may not be required. The applicant's "
                "premise that the proposal does not comply is incorrect."
            ),
        )
    )["verdict"]
    assert v2["status"] == "not_required"


def test_variance_supportable_band_unchanged() -> None:
    """ABS-377 regression guard on ABS-365 tailoring: a variance answer that
    concludes on supportability (no not-required signal) still renders the
    supportable/conditional band."""
    supportable = build_report(
        _purchase(
            question_slug="variance_justification",
            answer_text="The requested variance is permitted as-of-right on its merits.",
        )
    )["verdict"]
    assert supportable["status"] == "pass"
    assert supportable["label"] == "Supportable on all three statutory tests"

    conditional = build_report(
        _purchase(
            question_slug="variance_justification",
            answer_text="The variance is supportable, subject to conditions.",
        )
    )["verdict"]
    assert conditional["status"] == "conditional"
    assert conditional["label"] == "Supportable with conditions"


def test_verdict_label_falls_back_for_unknown_report_type() -> None:
    """An off-menu / unknown slug keeps the neutral use-permission wording."""
    v = build_report(
        _purchase(
            question_slug="other",
            answer_text="Permitted, subject to a development permit.",
        )
    )["verdict"]
    assert v["status"] == "conditional"
    assert v["label"] == "Permitted with conditions"


def test_ref_prefix_per_question() -> None:
    assert build_report(_purchase(question_slug="development_standards"))["ref"].startswith(
        "DS-"
    )
    assert build_report(_purchase(question_slug="variance_justification"))[
        "ref"
    ].startswith("VJ-")
