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


def test_heading_less_answer_degrades_to_prose() -> None:
    md = "Just a flat paragraph with no structure at all."
    summary, blocks = parse_blocks(md)
    assert summary == md
    # A pure-summary answer needs no duplicate prose block.
    assert all(b["type"] != "prose" or b["text"] != md for b in blocks)


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
    assert rep["zone_subtitle"] == report_mod.DEFAULT_ZONE_SUBTITLE
    assert rep["bylaw_version"] == report_mod.BYLAW_VERSION
    assert rep["price_cents"] == 19_900
    assert "DD-000321" in rep["footer"]


def test_zone_subtitle_can_be_lifted_from_metadata() -> None:
    p = _purchase(metadata_json={"zone_subtitle": "Downtown Core"})
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
