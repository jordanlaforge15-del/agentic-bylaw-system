"""ABS-332: the answer engine must never persist a value Postgres rejects.

The reported symptom was a raw HTTP 500 on a grounded, paid-for answer:
the user clicked "Get answer", waited through "Generating your answer…",
then saw *Couldn't run the answer (HTTP 500)*. Root cause: real bylaw
evidence is extracted from municipal PDFs that carry stray NUL (``0x00``)
bytes (and geometry tools can emit non-finite floats). Those values ride
untouched through ``Message.model_dump`` into the persisted answer —
``answer_text`` (a Postgres ``text`` column) and ``transcript_json`` (a
``jsonb`` column) — which are written by ``db.flush()`` *outside* the
engine-error guard in ``run_answer``. Postgres rejects the NUL
(``text fields cannot contain NUL`` / ``\\u0000 cannot be converted to
text``), and the rejection escapes as a 500.

SQLite (these unit tests) accepts the bytes, so we assert the *scrubbing*
happens here; the matching Postgres-backed proof that the 500 is gone
lives in ``web/e2e/functional/abs332-answer-nul-bytes.spec.ts``.
"""
from __future__ import annotations

import json
import math
from pathlib import Path

from advisor.billing import answers as answer_flow
from advisor.billing.answers import _serialize
from advisor.db.jsonsafe import json_safe, scrub_text
from advisor.db.models import QuestionPurchase, User
from advisor.llm import Message, TextBlock, ToolResultBlock, ToolUseBlock
from advisor.llm.base import LLMRole
from advisor.llm.mock import MockGateway
from advisor.llm.mock_dispatcher import build_dispatcher
from bylaw_retrieval.retrieval import RetrievalResponse
from layer1.db.init_db import create_all
from layer1.db.session import session_scope

NUL = "\x00"
PERSONA = "You are a test bylaw advisor."


class _StubRetrieval:
    """Grounds the answer (one non-error grounding tool call)."""

    def search(self, request):  # noqa: ANN001
        return RetrievalResponse(total_matches=1, matches=[], notes=[])


# -- scrub_text -------------------------------------------------------------


def test_scrub_text_removes_nul() -> None:
    assert scrub_text(f"set{NUL}back") == "setback"


def test_scrub_text_preserves_ordinary_whitespace() -> None:
    # Only the NUL is illegal in Postgres text/jsonb — tabs and newlines
    # are legal and must survive untouched.
    body = "line one\n\tindented\r\nline two"
    assert scrub_text(body) == body


# -- json_safe --------------------------------------------------------------


def test_json_safe_strips_nul_in_nested_strings_and_keys() -> None:
    cleaned = json_safe({f"k{NUL}ey": [f"a{NUL}b", {"deep": f"c{NUL}d"}]})
    assert NUL not in json.dumps(cleaned)
    assert cleaned == {"key": ["ab", {"deep": "cd"}]}


def test_json_safe_nulls_non_finite_floats() -> None:
    cleaned = json_safe(
        {"ratio": float("nan"), "dist": math.inf, "neg": -math.inf, "ok": 1.5}
    )
    assert cleaned == {"ratio": None, "dist": None, "neg": None, "ok": 1.5}


def test_serialize_yields_nul_free_jsonb_payload() -> None:
    # A tool_result carrying PDF-extracted evidence with a stray NUL, plus a
    # tool_use whose input echoes one — both must come out clean.
    messages = [
        Message(
            role=LLMRole.ASSISTANT,
            content=[
                ToolUseBlock(
                    id="t1",
                    name="search_bylaw_evidence",
                    input={"query": f"setback{NUL}rule"},
                )
            ],
        ),
        Message(
            role=LLMRole.USER,
            content=[
                ToolResultBlock(
                    tool_use_id="t1", content=f"Part II{NUL} setback is 7.5 m"
                )
            ],
        ),
        Message(role=LLMRole.ASSISTANT, content=[TextBlock(text=f"Answer{NUL}.")]),
    ]
    payload = _serialize(messages)
    # json.dumps escapes a real NUL to the six-char escape backslash-u-0-0-0-0; assert neither
    # the raw byte nor its JSON escape survive (Postgres rejects both).
    blob = json.dumps(payload)
    assert NUL not in blob
    assert "\\u0000" not in blob


# -- run_answer end to end (scrubbing wired into the grounded path) ----------


def _db_url(tmp_path: Path) -> str:
    return f"sqlite:///{tmp_path / 'advisor.db'}"


def _seed_user(db_url: str, *, free_questions: int) -> int:
    with session_scope(db_url) as s:
        u = User(
            clerk_user_id="u1", email="u1@x.com", free_questions_remaining=free_questions
        )
        s.add(u)
        s.flush()
        return u.id


async def test_run_answer_scrubs_nul_from_persisted_answer(tmp_path: Path) -> None:
    db_url = _db_url(tmp_path)
    create_all(db_url)
    uid = _seed_user(db_url, free_questions=1)
    with session_scope(db_url) as db:
        user = db.get(User, uid)
        purchase = answer_flow.start_question_free(
            db,
            user,
            question_slug="permitted_use",
            # The sentinel makes the mock ground with a NUL in the tool_use
            # input and answer with a NUL in the final text (ABS-332).
            inputs={"address": "1234 Elm St", "proposed_use": "a duplex MOCK_NUL_BYTES"},
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
        # Grounded and captured — the NUL did not derail settlement…
        assert p.status == "captured"
        assert p.answer_text
        # …and neither the answer nor the transcript carries a NUL, so the
        # row is writable to a real Postgres text/jsonb column.
        assert NUL not in p.answer_text
        assert NUL not in json.dumps(p.transcript_json)
