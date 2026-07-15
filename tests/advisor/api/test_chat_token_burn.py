"""ABS-383 — chat pre-flight token wallet: floor refusal, burn settlement,
``token_balance`` SSE.

The chat route bills the prepaid token wallet (``User.token_balance`` +
``advisor_token_transaction`` ledger) instead of case credits:

* A pre-flight floor check refuses a turn with 402 ``insufficient_tokens``
  when ``balance <= floor`` (before any ``llm_call`` UsageEvent is written).
* Settlement burns the turn's *measured* usage (input + output) as one
  ``burn`` ledger row linked to session / case / usage-event, with no refund
  heuristic — a mid-stream failure still burns what was recorded.
* Each turn emits a ``token_balance`` SSE event so the UI can decrement live.
* Tier / CaseCredit machinery is gone from the live chat path.

These stand up the real ``create_app`` FastAPI app over a fresh sqlite DB
with a ``DbSessionStore`` and a scripted ``MockGateway`` so the whole
pre-flight → stream → settlement pipeline is exercised end to end.
"""
from __future__ import annotations

import json
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from fastapi.testclient import TestClient
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session, sessionmaker

from advisor.api.app import create_app
from advisor.db.models import (
    Case,
    CaseCredit,
    ChatSession as DbChatSession,
    TokenTransaction,
    UsageEvent,
    User,
)
from advisor.llm import TokenUsage
from advisor.llm.mock import MockGateway, text_response
from layer1.db.init_db import create_all


# --------------------------------------------------------------------------
# Harness
# --------------------------------------------------------------------------
def _build_factory(db_url: str):
    engine = create_engine(db_url, future=True)
    factory = sessionmaker(bind=engine, expire_on_commit=False, future=True)

    @contextmanager
    def db_session_factory() -> Iterator[Session]:
        s = factory()
        try:
            yield s
            s.commit()
        except Exception:
            s.rollback()
            raise
        finally:
            s.close()

    return db_session_factory, factory


def _seed_user(
    factory,
    *,
    clerk_user_id: str = "clerk_burn",
    email: str = "burn@example.com",
    token_balance: int = 10_000,
    unlimited_credits: bool = False,
) -> int:
    """Seed a user with a pinned wallet balance.

    ``token_grant_issued`` is pre-set so the header-auth resolver's
    signup-grant self-heal is a no-op and the balance stays exactly as
    seeded (the grant would otherwise add 25k tokens on first sight).
    """
    s = factory()
    try:
        user = User(
            clerk_user_id=clerk_user_id,
            email=email,
            full_name="Burn Tester",
            token_balance=token_balance,
            unlimited_credits=unlimited_credits,
            requests_per_minute_limit=600,
            metadata_json={"token_grant_issued": True},
        )
        s.add(user)
        s.commit()
        return user.id
    finally:
        s.close()


def _seed_case(
    factory,
    *,
    user_id: int,
    current_tier: str | None = None,
    case_number: int = 1,
) -> int:
    s = factory()
    try:
        case = Case(
            user_id=user_id,
            user_case_number=case_number,
            anchor_label="1234 Main St, Halifax",
            anchor_key="1234 main st halifax",
            anchor_kind="address",
            current_tier=current_tier,
        )
        s.add(case)
        s.commit()
        return case.id
    finally:
        s.close()


def _accept_terms(factory, user_id: int) -> None:
    """Record current-terms acceptance so the 412 click-wrap gate passes."""
    from advisor.legal import get_current_terms, record_acceptance

    s = factory()
    try:
        user = s.get(User, user_id)
        record_acceptance(
            s,
            user=user,
            version_hash=get_current_terms().version_hash,
            ip=None,
            user_agent=None,
        )
        s.commit()
    finally:
        s.close()


def _make_app(db_session_factory, gateway: MockGateway) -> TestClient:
    app = create_app(
        gateway=gateway,
        retrieval_service_factory=lambda: None,
        db_session_factory=db_session_factory,
        persona_text="be helpful",
    )
    return TestClient(app)


def _parse_sse(text: str) -> list[dict]:
    events: list[dict] = []
    current: dict = {}
    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            if current:
                events.append(current)
                current = {}
            continue
        if line.startswith("event:"):
            current["event"] = line[len("event:"):].strip()
        elif line.startswith("data:"):
            current["data"] = line[len("data:"):].strip()
    if current:
        events.append(current)
    return events


def _post_chat(
    client: TestClient,
    *,
    user_id: str = "clerk_burn",
    message: str = "What is the minimum front yard setback?",
    case_id: int | None = None,
    session_id: str | None = None,
):
    body: dict = {"message": message}
    if case_id is not None:
        body["case_id"] = case_id
    if session_id is not None:
        body["session_id"] = session_id
    return client.post(
        "/v1/chat",
        json=body,
        headers={"X-Test-User-Id": user_id},
    )


def _event(events: list[dict], name: str) -> dict | None:
    for e in events:
        if e.get("event") == name:
            return e
    return None


def _scripted(input_tokens: int, output_tokens: int) -> MockGateway:
    """A single end_turn text response with a pinned usage — one iteration,
    no tool_use, so ``last_turn_usage`` == the pinned counts exactly."""
    return MockGateway(
        scripted=[
            text_response(
                "Based on the bylaw evidence, here is the answer.",
                usage=TokenUsage(
                    input_tokens=input_tokens, output_tokens=output_tokens
                ),
                stop_reason="end_turn",
            )
        ]
    )


# --------------------------------------------------------------------------
# Happy path: balance 10,000, floor 0, usage 1,200 in / 300 out
# --------------------------------------------------------------------------
def test_burn_happy_path(tmp_path: Path) -> None:
    db_url = f"sqlite:///{tmp_path / 'a.db'}"
    create_all(db_url)
    db_session_factory, factory = _build_factory(db_url)
    user_id = _seed_user(factory, token_balance=10_000)
    _accept_terms(factory, user_id)
    case_id = _seed_case(factory, user_id=user_id)

    client = _make_app(db_session_factory, _scripted(1_200, 300))
    resp = _post_chat(client, case_id=case_id)
    assert resp.status_code == 200, resp.text
    events = _parse_sse(resp.text)

    tb = _event(events, "token_balance")
    assert tb is not None, f"expected token_balance SSE, got {events}"
    payload = json.loads(tb["data"])
    assert payload["balance_tokens"] == 8_500
    assert payload["burned_tokens"] == 1_500
    assert payload["approx_turns_remaining"] == 3  # 8500 // 2500
    assert payload["low_balance"] is False  # 8500 > 5000 warn threshold
    assert payload["warn_threshold_tokens"] == 5_000

    # Exactly one burn ledger row of -1500, linked to session / case / usage.
    s = factory()
    try:
        burns = (
            s.execute(
                select(TokenTransaction).where(
                    TokenTransaction.entry_type == "burn"
                )
            )
            .scalars()
            .all()
        )
        assert len(burns) == 1
        burn = burns[0]
        assert burn.amount_tokens == -1_500
        assert burn.balance_after == 8_500
        assert burn.case_id == case_id
        assert burn.session_id is not None
        assert burn.usage_event_id is not None

        # Case.tokens_consumed bumped by the turn's input+output.
        case = s.get(Case, case_id)
        assert case.tokens_consumed == 1_500

        # Wallet balance moved on the user row too.
        user = s.get(User, user_id)
        assert user.token_balance == 8_500
    finally:
        s.close()


# --------------------------------------------------------------------------
# Overdraw then next-turn 402
# --------------------------------------------------------------------------
def test_overdraw_then_next_turn_402(tmp_path: Path) -> None:
    db_url = f"sqlite:///{tmp_path / 'a.db'}"
    create_all(db_url)
    db_session_factory, factory = _build_factory(db_url)
    user_id = _seed_user(factory, token_balance=200)
    _accept_terms(factory, user_id)
    case_id = _seed_case(factory, user_id=user_id)

    # First turn burns 1,000 (700 in + 300 out); 200 > floor 0 so it runs.
    client = _make_app(db_session_factory, _scripted(700, 300))
    resp = _post_chat(client, case_id=case_id)
    assert resp.status_code == 200, resp.text
    events = _parse_sse(resp.text)
    tb = json.loads(_event(events, "token_balance")["data"])
    assert tb["balance_tokens"] == -800
    assert tb["burned_tokens"] == 1_000

    session_id = json.loads(_event(events, "session")["data"])["session_id"]

    # Second turn: balance -800 <= floor 0 → 402 before any stream starts.
    client2 = _make_app(db_session_factory, _scripted(700, 300))
    resp2 = _post_chat(client2, case_id=case_id, session_id=session_id)
    assert resp2.status_code == 402, resp2.text
    detail = resp2.json()["detail"]
    assert detail["code"] == "insufficient_tokens"
    assert detail["balance_tokens"] == -800
    assert detail["floor_tokens"] == 0
    assert detail["approx_turns_remaining"] == 0


# --------------------------------------------------------------------------
# Balance == floor: 402 before any UsageEvent llm_call row is written
# --------------------------------------------------------------------------
def test_at_floor_refuses_before_usage_event(tmp_path: Path) -> None:
    db_url = f"sqlite:///{tmp_path / 'a.db'}"
    create_all(db_url)
    db_session_factory, factory = _build_factory(db_url)
    user_id = _seed_user(factory, token_balance=0)
    _accept_terms(factory, user_id)
    case_id = _seed_case(factory, user_id=user_id)

    client = _make_app(db_session_factory, _scripted(100, 100))
    resp = _post_chat(client, case_id=case_id)
    assert resp.status_code == 402, resp.text
    assert resp.json()["detail"]["code"] == "insufficient_tokens"

    s = factory()
    try:
        llm_calls = s.execute(
            select(func.count(UsageEvent.id)).where(
                UsageEvent.event_type == "llm_call"
            )
        ).scalar_one()
        assert llm_calls == 0, "no llm_call UsageEvent may be written at floor"
    finally:
        s.close()


# --------------------------------------------------------------------------
# unlimited_credits bypasses the floor and the burn
# --------------------------------------------------------------------------
def test_unlimited_credits_no_floor_no_burn(tmp_path: Path) -> None:
    db_url = f"sqlite:///{tmp_path / 'a.db'}"
    create_all(db_url)
    db_session_factory, factory = _build_factory(db_url)
    user_id = _seed_user(
        factory, token_balance=-5_000, unlimited_credits=True
    )
    _accept_terms(factory, user_id)
    case_id = _seed_case(factory, user_id=user_id)

    client = _make_app(db_session_factory, _scripted(1_200, 300))
    resp = _post_chat(client, case_id=case_id)
    assert resp.status_code == 200, resp.text  # no 402 despite negative balance

    s = factory()
    try:
        burns = s.execute(
            select(func.count(TokenTransaction.id)).where(
                TokenTransaction.entry_type == "burn"
            )
        ).scalar_one()
        assert burns == 0, "unlimited users burn no wallet tokens"

        user = s.get(User, user_id)
        assert user.token_balance == -5_000  # unchanged

        # UsageEvent still records the real tokens.
        row = s.execute(
            select(UsageEvent).where(UsageEvent.event_type == "llm_call")
        ).scalar_one()
        assert row.tokens_input == 1_200
        assert row.tokens_output == 300
    finally:
        s.close()


# --------------------------------------------------------------------------
# Stream failure mid-turn: usage still burned, chat_error precedes settlement
# --------------------------------------------------------------------------
def test_stream_error_still_burns(tmp_path: Path, monkeypatch) -> None:
    db_url = f"sqlite:///{tmp_path / 'a.db'}"
    create_all(db_url)
    db_session_factory, factory = _build_factory(db_url)
    user_id = _seed_user(factory, token_balance=10_000)
    _accept_terms(factory, user_id)
    case_id = _seed_case(factory, user_id=user_id)

    # The tool loop (gateway.complete) records usage, THEN the synthetic
    # replay stream raises — exercising the "usage already recorded, stream
    # fails" path. Patch the shared synthetic streamer to blow up after the
    # first event.
    from advisor.llm.base import LLMRole, MessageStartEvent

    async def _broken_stream(response):
        yield MessageStartEvent(
            message_id=response.id, model=response.model, role=LLMRole.ASSISTANT
        )
        raise RuntimeError("boom mid-stream")

    monkeypatch.setattr(
        "advisor.llm.mock.MockGateway._stream_from_response",
        staticmethod(_broken_stream),
    )

    client = _make_app(db_session_factory, _scripted(1_200, 300))
    resp = _post_chat(client, case_id=case_id)
    assert resp.status_code == 200, resp.text
    events = _parse_sse(resp.text)
    names = [e.get("event") for e in events]
    assert "chat_error" in names
    assert "token_balance" in names
    # chat_error precedes settlement.
    assert names.index("chat_error") < names.index("token_balance")

    s = factory()
    try:
        burn = s.execute(
            select(TokenTransaction).where(
                TokenTransaction.entry_type == "burn"
            )
        ).scalar_one()
        assert burn.amount_tokens == -1_500  # recorded usage still burned
    finally:
        s.close()


# --------------------------------------------------------------------------
# Free-path case (current_tier=None): no 400, no CaseCredit rows
# --------------------------------------------------------------------------
def test_free_path_case_no_credit_rows(tmp_path: Path) -> None:
    db_url = f"sqlite:///{tmp_path / 'a.db'}"
    create_all(db_url)
    db_session_factory, factory = _build_factory(db_url)
    user_id = _seed_user(factory, token_balance=10_000)
    _accept_terms(factory, user_id)
    case_id = _seed_case(factory, user_id=user_id, current_tier=None)

    client = _make_app(db_session_factory, _scripted(140, 90))
    resp = _post_chat(client, case_id=case_id)
    assert resp.status_code == 200, resp.text
    events = _parse_sse(resp.text)
    # Preamble carries case context with a null tier.
    session_payload = json.loads(_event(events, "session")["data"])
    assert session_payload["case_id"] == case_id
    assert session_payload["case_number"] == 1
    assert session_payload["tier"] is None

    s = factory()
    try:
        credits = s.execute(
            select(func.count(CaseCredit.id))
        ).scalar_one()
        assert credits == 0, "free-path chat must not mint CaseCredit rows"

        # DB session's per-tier budget stays None (no-op decrement).
        chat_row = s.execute(select(DbChatSession)).scalar_one()
        assert chat_row.token_budget_remaining is None
    finally:
        s.close()


# --------------------------------------------------------------------------
# low_balance flag flips true below the warn threshold
# --------------------------------------------------------------------------
def test_low_balance_flag(tmp_path: Path) -> None:
    db_url = f"sqlite:///{tmp_path / 'a.db'}"
    create_all(db_url)
    db_session_factory, factory = _build_factory(db_url)
    # Start at 4,000; burn 230 → 3,770 which is below the 5,000 warn floor.
    user_id = _seed_user(factory, token_balance=4_000)
    _accept_terms(factory, user_id)
    case_id = _seed_case(factory, user_id=user_id)

    client = _make_app(db_session_factory, _scripted(140, 90))
    resp = _post_chat(client, case_id=case_id)
    assert resp.status_code == 200, resp.text
    tb = json.loads(_event(_parse_sse(resp.text), "token_balance")["data"])
    assert tb["balance_tokens"] == 3_770
    assert tb["low_balance"] is True


# --------------------------------------------------------------------------
# Retired upgrade / budget-warning surfaces never emit
# --------------------------------------------------------------------------
def test_no_upgrade_or_budget_warning_events(tmp_path: Path) -> None:
    db_url = f"sqlite:///{tmp_path / 'a.db'}"
    create_all(db_url)
    db_session_factory, factory = _build_factory(db_url)
    user_id = _seed_user(factory, token_balance=10_000)
    _accept_terms(factory, user_id)
    case_id = _seed_case(factory, user_id=user_id)

    client = _make_app(db_session_factory, _scripted(140, 90))
    resp = _post_chat(client, case_id=case_id)
    names = [e.get("event") for e in _parse_sse(resp.text)]
    assert "case_upgrade_offer" not in names
    assert "case_budget_warning" not in names


# --------------------------------------------------------------------------
# In-memory store (no db_session_factory) still skips billing entirely
# --------------------------------------------------------------------------
def test_in_memory_path_skips_billing() -> None:
    gateway = _scripted(1_200, 300)
    app = create_app(gateway=gateway, persona_text="be helpful")
    client = TestClient(app)
    resp = client.post(
        "/v1/chat",
        json={"message": "hi"},
        headers={"X-Test-User-Id": "mem-user"},
    )
    assert resp.status_code == 200, resp.text
    names = [e.get("event") for e in _parse_sse(resp.text)]
    assert "session" in names
    assert "token_balance" not in names  # no wallet without a DB


# --------------------------------------------------------------------------
# Import-boundary guard: the chat route no longer imports the credit lifecycle
# --------------------------------------------------------------------------
def test_chat_route_does_not_import_credit_lifecycle() -> None:
    """ABS-383: the chat route module must not reference the retired case-
    credit lifecycle helpers. A future edit that re-introduces one fails
    here loudly (mirrors the ABS-324 import-boundary guard style)."""
    import ast

    path = (
        Path(__file__).resolve().parents[3]
        / "src"
        / "advisor"
        / "api"
        / "app.py"
    )
    tree = ast.parse(path.read_text(), filename=str(path))
    referenced: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            for alias in node.names:
                referenced.add(alias.name)
                if alias.asname:
                    referenced.add(alias.asname)
        elif isinstance(node, ast.Name):
            referenced.add(node.id)
        elif isinstance(node, ast.Attribute):
            referenced.add(node.attr)
    forbidden = {
        "reserve_credit_for_session",
        "commit_credit_for",
        "refund_credit_for",
    }
    leaked = referenced & forbidden
    assert not leaked, (
        f"chat route re-introduced credit lifecycle symbols {sorted(leaked)}; "
        "the live chat path bills the token wallet, not case credits."
    )


# --------------------------------------------------------------------------
# max_iterations defaults to ADVISOR_CHAT_MAX_ITERATIONS (20) when tier is None
# --------------------------------------------------------------------------
def test_tierless_session_uses_default_max_iterations(monkeypatch) -> None:
    """A tier-less (wallet-billed) chat session runs the tool loop with the
    env-configurable ``ADVISOR_CHAT_MAX_ITERATIONS`` default (20), not the
    legacy hardcoded 10."""
    import asyncio

    from advisor.chat import session as session_mod

    captured: dict = {}

    async def _fake_run_tool_loop(gateway, *, request, handlers, **kwargs):
        captured["max_iterations"] = kwargs.get("max_iterations")

        class _Result:
            final_response = text_response("done", stop_reason="end_turn")
            conversation: list = []
            total_usage = TokenUsage(input_tokens=1, output_tokens=1)
            circuit_trip = None
            iterations = 1
            terminated_reason = "end_turn"
            per_iteration: list = []
            tool_calls: list = []

        return _Result()

    monkeypatch.setattr(session_mod, "run_tool_loop", _fake_run_tool_loop)
    monkeypatch.delenv("ADVISOR_CHAT_MAX_ITERATIONS", raising=False)

    chat = session_mod.ChatSession(
        session_id="1",
        user_id="u",
        system_prompt="be helpful",
        model="claude-opus-4-5",
        tier=None,
    )
    asyncio.run(chat.send_user_message_blocking(object(), "hi"))
    assert captured["max_iterations"] == 20
