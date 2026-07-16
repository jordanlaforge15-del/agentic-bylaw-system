"""ABS-376: the dev-standards report must always evaluate use permission.

The reopened bug: DS-000020 (pre-ABS-375) led with the critical use-permission
threshold — multi-unit dwelling use in the INS zone is conditionally permitted
only on a Schedule 9 landmark site (Section 43), the go/no-go question for the
whole proposal. After the ABS-375 prompt refocus (`4074a6e`, "Point
dev-standards + variance prompts at resolved data") the report narrowed to the
resolved built-form facts and DS-000024 (identical repro input) dropped use
permission entirely — no use-permission section, no Schedule 9 reference, not
even in "Unresolved items". A $149 compliance answer that evaluates *how* the
building complies while silently skipping *whether* the use is allowed is
materially incomplete.

The fix (`src/advisor/billing/questions.py`) makes a use-permission evaluation a
MANDATORY leading section of the dev-standards prompt, before built-form
standards, with an explicit "cannot resolve — Schedule 9 absent from corpus"
fallback citing the ingestion-gap doc.

This test guards the report STRUCTURE end-to-end: it runs the REAL
``run_answer`` engine loop against the ``MockGateway`` — which emits the exact
dev-standards report shape the SKU now produces (``MOCK_DEV_STANDARDS_REPORT``,
a use-permission section AHEAD of the built-form table) — then builds the report
the product surface renders and asserts the use-permission block is present,
leads the built-form standards, and names Section 43 / Schedule 9. Deleting the
use-permission section from the mock (or the parser dropping it) fails the test.
The rendered contract is guarded over Postgres in
``web/e2e/functional/abs376-dev-standards-use-permission.spec.ts``.
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


def _block_titles(report: dict) -> list[str]:
    return [b.get("title", "") for b in report["blocks"]]


async def test_dev_standards_report_carries_a_use_permission_block(
    tmp_path: Path,
) -> None:
    db_url = _db_url(tmp_path)
    create_all(db_url)
    uid = _seed_user(db_url)

    with session_scope(db_url) as db:
        user = db.get(User, uid)
        purchase = answer_flow.start_question_free(
            db,
            user,
            question_slug="development_standards",
            # The ticket's repro input: the 1250 Robie St 4-storey / 14.5 m /
            # 12-unit proposal. The sentinel rides the free-form
            # project_details into the rendered prompt, steering the mock to
            # answer with the use-permission-first report shape — no product
            # code path is altered.
            inputs={
                "address": "1250 Robie St, Halifax",
                "project_details": (
                    "Proposed 4-storey multi-unit residential building: "
                    "14.5 m height, 12 units, front setback 3.0 m, rear "
                    "setback 5.0 m, side setbacks 1.5 m each, lot coverage "
                    "48%, 8 parking spaces. MOCK_DEV_STANDARDS_REPORT"
                ),
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
        assert p.status == "captured", p.failure_reason

        report = build_report(p)
        assert report is not None

        titles = _block_titles(report)

        # A use-permission block exists — the section ABS-375 dropped.
        use_idx = next(
            (
                i
                for i, t in enumerate(titles)
                if "use" in t.lower() and "permission" in t.lower()
            ),
            None,
        )
        assert use_idx is not None, (
            "dev-standards report is missing its mandatory use-permission "
            f"block — blocks were {titles!r}"
        )

        # It leads the built-form standards — use permission is the go/no-go
        # gate and must come BEFORE how-it-complies. Find the built-form
        # table's position and assert the use block precedes it.
        builtform_idx = next(
            (
                i
                for i, b in enumerate(report["blocks"])
                if b.get("type") == "table"
                and "built" in (b.get("title") or "").lower()
            ),
            None,
        )
        assert builtform_idx is not None, "expected a built-form standards table"
        assert use_idx < builtform_idx, (
            "use permission must be evaluated BEFORE built-form standards "
            f"(use block at {use_idx}, built-form table at {builtform_idx})"
        )

        # The block names the governing threshold: Section 43 / Schedule 9 for
        # multi-unit use in INS — the DS-000020 finding DS-000024 dropped.
        use_block = report["blocks"][use_idx]
        use_blob = json.dumps(use_block)
        assert "Section 43" in use_blob, use_block
        assert "Schedule 9" in use_blob, use_block
        assert "INS" in use_blob, use_block
