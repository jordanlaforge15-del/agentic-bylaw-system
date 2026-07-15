"""ABS-359: the paid report deliverable must never carry agent chatter.

The reported symptom was chain-of-thought surfacing in the report Summary of
a purchased, exportable deliverable: the engine's markdown opens with a
first-person planning monologue ("I have gathered the key provisions … Let me
compile the findings:") fenced off by a ``---`` rule, sprinkles decorative
``---`` between sections, and tacks a conversational apology/sign-off ("I
apologize that I cannot provide … Would you like me to research a different
property?") onto the final section. ``build_report`` (parse_blocks) must
scrub all of it.

The first fix's spec stubbed an already-clean report, so a monologue leak
sailed straight through it (the re-test that reopened the ticket). This test
closes that gap by running the REAL ``run_answer`` engine loop against the
``MockGateway`` — which emits the exact monologue shape a report SKU produces
(``MOCK_MONOLOGUE_REPORT``) — then building the report the product surface
renders from the captured answer and asserting it is clean. The rendered
contract is guarded end-to-end over Postgres in
``web/e2e/functional/abs359-report-parser.spec.ts``; the parser units live in
``tests/advisor/billing/test_report.py``.
"""
from __future__ import annotations

import json
from pathlib import Path

from advisor.billing import answers as answer_flow
from advisor.billing.report import build_report
from advisor.db.models import QuestionPurchase, User
from advisor.llm.mock import MockGateway
from advisor.llm.mock_dispatcher import build_dispatcher
from bylaw_retrieval.retrieval import RetrievalResponse
from layer1.db.init_db import create_all
from layer1.db.session import session_scope

PERSONA = "You are a test bylaw advisor."

# Chain-of-thought / scaffolding that must never reach the deliverable.
_FORBIDDEN = (
    "Let me compile",
    "I have gathered",
    "sufficient information",
    "I apologize",
    "Would you like me to",
    "---",
)


class _StubRetrieval:
    """Grounds the answer (one non-error grounding tool call)."""

    def search(self, request):  # noqa: ANN001
        return RetrievalResponse(total_matches=1, matches=[], notes=[])


def _db_url(tmp_path: Path) -> str:
    return f"sqlite:///{tmp_path / 'advisor.db'}"


def _seed_user(db_url: str) -> int:
    with session_scope(db_url) as s:
        u = User(clerk_user_id="u1", email="u1@x.com", free_questions_remaining=1)
        s.add(u)
        s.flush()
        return u.id


async def test_built_report_scrubs_agent_monologue_end_to_end(tmp_path: Path) -> None:
    db_url = _db_url(tmp_path)
    create_all(db_url)
    uid = _seed_user(db_url)

    with session_scope(db_url) as db:
        user = db.get(User, uid)
        purchase = answer_flow.start_question_free(
            db,
            user,
            question_slug="variance_justification",
            # The sentinel rides the free-form hardship_rationale into the
            # rendered prompt, steering the mock to answer with the monologue
            # shape — no product code path is altered.
            inputs={
                "address": "5686 Spring Garden Rd",
                "requested_variance": "Rear-yard setback reduced from 6.0 m to 5.6 m.",
                "hardship_rationale": "Irregular lot depth. MOCK_MONOLOGUE_REPORT",
            },
        )
        pid = purchase.id

    with session_scope(db_url) as db:
        p = db.get(QuestionPurchase, pid)
        p = await answer_flow.run_answer(
            db,
            p,
            gateway=MockGateway(callable_=build_dispatcher()),
            persona=PERSONA,
            retrieval_factory=_StubRetrieval(),
            client=None,
        )
        assert p.status == "captured"
        # The RAW captured answer really does carry the monologue — proof the
        # mock drove the chain-of-thought shape, not a pre-cleaned fixture.
        assert "Let me compile the findings" in p.answer_text
        assert "I apologize that I cannot provide" in p.answer_text

        report = build_report(p)
        assert report is not None

        # Summary is the report's own lead, not the planning monologue.
        assert report["summary"] == (
            "The requested rear-yard variance is supportable on all three "
            "statutory tests."
        )

        # No chatter or stray rule survives ANYWHERE in the deliverable.
        blob = json.dumps(report)
        for forbidden in _FORBIDDEN:
            assert forbidden not in blob, f"report leaks: {forbidden!r}"

        # The fix strips chatter, it does not gut the report.
        assert "statutory" in blob


async def test_built_report_drops_dangling_list_preamble_end_to_end(
    tmp_path: Path,
) -> None:
    """ABS-374: a section that announces a list ("The complete list is:") and
    then emits no items must not leave the dangling colon sentence in the paid
    deliverable. Runs the REAL run_answer loop against the MockGateway (which
    emits the PU-000019 shape via MOCK_DANGLING_LIST_REPORT), then builds the
    report the product surface renders and asserts the announcement is gone
    while the real prose either side survives. The rendered contract is guarded
    end-to-end over Postgres in
    ``web/e2e/functional/abs374-dangling-list.spec.ts``; the parser units live
    in ``tests/advisor/billing/test_report.py``."""
    db_url = _db_url(tmp_path)
    create_all(db_url)
    uid = _seed_user(db_url)

    with session_scope(db_url) as db:
        user = db.get(User, uid)
        purchase = answer_flow.start_question_free(
            db,
            user,
            question_slug="permitted_use",
            # The sentinel rides the free-form proposed_use into the rendered
            # prompt, steering the mock to the dangling-list shape.
            inputs={
                "address": "5184 Morris St",
                "proposed_use": "a home-based child daycare. MOCK_DANGLING_LIST_REPORT",
            },
        )
        pid = purchase.id

    with session_scope(db_url) as db:
        p = db.get(QuestionPurchase, pid)
        p = await answer_flow.run_answer(
            db,
            p,
            gateway=MockGateway(callable_=build_dispatcher()),
            persona=PERSONA,
            retrieval_factory=_StubRetrieval(),
            client=None,
        )
        assert p.status == "captured"
        # The RAW captured answer really does carry the dangling announcement —
        # proof the mock drove the broken shape, not a pre-cleaned fixture.
        assert "The complete list is:" in p.answer_text

        report = build_report(p)
        assert report is not None

        blob = json.dumps(report)
        # The dangling announcement is gone everywhere in the deliverable.
        assert "complete list is" not in blob, "report leaks the dangling preamble"
        # No block (or the summary) dangles on a bare colon.
        for block in report["blocks"]:
            text = (block.get("text") or block.get("body") or "").strip()
            assert not text.endswith(":"), f"block dangles on a colon: {text!r}"
        assert not report["summary"].rstrip().endswith(":")
        # The fix strips only the announcement — real content either side stays.
        assert "Section 51 lists the permitted home occupation uses" in blob
        assert "Child daycare is not listed" in blob
