"""End-to-end UI test entrypoint for the Halifax Bylaw Advisor API.

Run with::

    uvicorn advisor.api.e2e_server:app --host 127.0.0.1 --port 8001

Differences from ``advisor.api.dev`` and ``advisor.api.main``:

* **LLM gateway is always a ``MockGateway``** wired to
  ``advisor.llm.mock_dispatcher.build_dispatcher()``. Tests never call
  out to Anthropic, so no API key is required and responses are
  byte-deterministic — important for SSE streams whose content gets
  asserted in Playwright.
* **DB session store is enabled** (``db_session_factory=session_scope``)
  so the case-credit lifecycle, quota enforcement, and session
  persistence run for real against the test Postgres database. The
  ``DATABASE_URL`` env var must point at the test DB (default name
  ``layer1_test``).
* **No Clerk verifier.** The chat / cases / admin routers fall back to
  the ``X-Test-User-Id`` header — same path the Next.js proxy uses
  when ``CLERK_SECRET_KEY`` is unset.
* **Permissive CORS** for the Next.js dev server at
  ``http://localhost:3001`` (default; override via
  ``ADVISOR_E2E_CORS_ORIGINS``).

Never wire this entrypoint to production traffic — there is no auth.
"""
from __future__ import annotations

import logging
import os
import sys
import uuid
from pathlib import Path
from typing import Any, Literal
from unittest.mock import MagicMock

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

# ABS-75: bring the abs-learning agents package onto sys.path so the
# test-only discovery endpoint can import DiscoveryAgent. This module is
# only ever imported by the e2e entrypoint — production (advisor.api.main)
# never sees this file, so the sys.path insertion is scoped to tests.
_ABS_LEARNING_SRC = Path(__file__).resolve().parents[3] / "abs-learning" / "src"
if _ABS_LEARNING_SRC.is_dir():
    _abs_learning_path = str(_ABS_LEARNING_SRC)
    if _abs_learning_path not in sys.path:
        sys.path.insert(0, _abs_learning_path)

from advisor.api.app import create_app
from advisor.db.models import InviteRequest, User
from advisor.llm.mock import MockGateway
from advisor.llm.mock_dispatcher import build_dispatcher
from bylaw_retrieval.retrieval import LocationSlot, RetrievalService
from layer1.db.base import Document, SemanticEntity, SourceFragment, utcnow
from layer1.db.session import session_scope
from layer1.manifest_adapter import (
    ManifestNotReadyError,
    load_manifest,
    profile_from_manifest,
)
from layer1.models.enums import IngestionStatus, ParseStatus
from layer1.pipeline.ingest import ingest_file
from layer1.profiles import HALIFAX_PROFILE
from layer1.semantic.enrichment import enrich_document_semantics
from layer2.compliance.db.models import SubmissionAttributeSource
from layer2.compliance.evaluator import (
    DocumentFilters,
    EvaluationRequest,
    EvaluatorService,
    SubmissionAttributeInput,
)

logger = logging.getLogger(__name__)


def build_e2e_app() -> FastAPI:
    """Construct the test FastAPI app wired for end-to-end UI tests."""
    gateway = MockGateway(callable_=build_dispatcher())

    # ABS-53: wire a real EvaluatorService into the submissions router
    # so the /submissions/{id}/evaluate endpoint actually evaluates. The
    # factory is called per-request with the active DB session so each
    # evaluator run gets its own retrieval-service handle.
    def _submissions_evaluator_factory(session):
        retrieval = RetrievalService(session)
        evaluator = EvaluatorService(session, retrieval_service=retrieval)
        return evaluator.evaluate

    app = create_app(
        gateway=gateway,
        verifier=None,
        db_session_factory=session_scope,
        submissions_evaluator_factory=_submissions_evaluator_factory,
    )

    origins_env = os.environ.get(
        "ADVISOR_E2E_CORS_ORIGINS", "http://localhost:3001"
    )
    origins = [o.strip() for o in origins_env.split(",") if o.strip()]
    app.add_middleware(
        CORSMiddleware,
        allow_origins=origins,
        allow_credentials=False,
        allow_methods=["GET", "POST", "OPTIONS"],
        allow_headers=[
            "Content-Type",
            "X-Test-User-Id",
            "X-Test-User-Email",
            "X-Test-User-Full-Name",
            "Authorization",
            "Last-Event-ID",
        ],
        expose_headers=["X-Session-Id"],
    )

    _mount_test_router(app)

    logger.warning(
        "advisor.api.e2e_server is running with MockGateway and the "
        "X-Test-User-Id header fallback. This entrypoint MUST NOT be "
        "exposed to the public internet."
    )
    return app


# ---------------------------------------------------------------------------
# Test-only auth-lifecycle router (mounted only by the e2e entrypoint).
#
# The Playwright suite uses these endpoints to drive the sign-up /
# admin-approval / first-login lifecycle that production wires through
# Clerk's allowlist + the /api/admin/invites/{id}/approve route. Both
# of those depend on Clerk being configured (JWKS, Backend API,
# allowlist) — none of which the e2e stack provides. By exposing a
# direct InviteRequest writer here, the auth specs can exercise the
# *post-Clerk* code path (resolve_or_create user, invite redemption,
# starter-credit gift) without re-hosting Clerk in tests.
#
# Endpoints are namespaced ``/v1/_test/...`` so they're impossible to
# confuse with the production surface. ``advisor.api.main`` never
# imports this module — the routes literally cannot exist outside the
# e2e entrypoint.
# ---------------------------------------------------------------------------


class _ApproveInviteBody(BaseModel):
    # Plain str to avoid the email-validator runtime dep — same choice
    # the rest of the codebase makes (see advisor.db.schemas).
    email: str = Field(min_length=3, max_length=320)
    name: str = Field(min_length=1, max_length=200)
    starter_credits: int = Field(default=0, ge=0, le=1000)
    starter_tier: Literal["quick", "standard", "complex"] = "standard"


class _ResetUserBody(BaseModel):
    clerk_user_id: str = Field(min_length=1, max_length=255)


# ABS-46: test-only evaluator endpoint. Bypasses the chat / SSE
# pipeline so the Playwright spec can assert directly on the
# structured compliance matrix the evaluate_submission_against_bylaws
# MCP tool returns. The chat-layer path is already covered by the
# pytest suite in ``tests/test_evaluator_mcp_tool.py``; the spec's
# job is to exercise the path through real-stack HTTP + Postgres so a
# misconfigured proxy or missing migration would trip e2e.
class _EvaluateBylawsAttribute(BaseModel):
    attribute_key: str = Field(min_length=1, max_length=128)
    value: object | None = None
    unit: str | None = None
    source: Literal["manual", "extracted", "derived", "override"] = "manual"
    confidence: float = Field(default=1.0, ge=0.0, le=1.0)


class _EvaluateBylawsBody(BaseModel):
    attributes: list[_EvaluateBylawsAttribute] = Field(min_length=1)
    location: dict[str, object] | None = None
    document_filters: dict[str, object] | None = None
    per_attribute_limit: int = Field(default=8, ge=1, le=25)
    submission_id: int | None = None
    persist_decision: bool = False


# ABS-75: test-only Discovery Agent endpoint. Exercises the classifier →
# parent-resolution → SourceDocument assembly path through the real FastAPI
# stack. The crawler half (HTTP fetch + link extraction) is unit-tested in
# abs-learning/tests/test_discovery.py against an httpx MockTransport; here
# the spec POSTs pre-crawled candidates + canned LLM tool-use payloads, and
# the endpoint runs the same data-plane code the production agent runs.
# Keeps the spec hermetic (no live web, no real Anthropic calls) while
# proving the FastAPI proxy + abs-learning package wiring still works.
class _DiscoveryCandidateBody(BaseModel):
    url: str = Field(min_length=1, max_length=2048)
    link_text: str = Field(default="", max_length=512)
    discovered_on: str = Field(default="", max_length=2048)
    page_title: str | None = None
    content_type: str | None = None
    is_pdf: bool = False


class _DiscoveryMunicipalityBody(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    jurisdiction_code: str = Field(min_length=1, max_length=64)
    province: str = Field(min_length=1, max_length=64)
    governing_body: str | None = None


class _DiscoveryBody(BaseModel):
    municipality: _DiscoveryMunicipalityBody
    candidates: list[_DiscoveryCandidateBody] = Field(min_length=1, max_length=64)
    classifier_responses: list[list[dict[str, object]]] = Field(min_length=1)
    parent_links: list[dict[str, object]] | None = None
    confidence_floor: float = Field(default=0.4, ge=0.0, le=1.0)
    batch_size: int = Field(default=15, ge=1, le=64)


# ABS-74: test-only manifest-driven ingest endpoint.
# Exercises the full Phase 2 → Layer 1 wiring through the real FastAPI stack:
# the Playwright spec POSTs a manifest path + a bylaw text path, the endpoint
# loads the manifest, derives a ParsingProfile via the adapter, ingests, runs
# semantic enrichment with the manifest overlay, and returns a small summary
# the spec can assert on. The chat-loop / SSE machinery is bypassed because
# we're proving the *ingestion* path; the chat-side coverage already exists.
class _ManifestIngestBody(BaseModel):
    manifest_path: str = Field(min_length=1, max_length=1024)
    bylaw_path: str = Field(min_length=1, max_length=1024)
    bylaw_name: str = Field(min_length=1, max_length=256)


def _mount_test_router(app: FastAPI) -> None:
    """Wire the ``/v1/_test/...`` lifecycle endpoints onto ``app``."""

    @app.post("/v1/_test/invite-approve")
    async def invite_approve(body: _ApproveInviteBody) -> dict[str, object]:
        """Insert an ``approved`` ``InviteRequest`` for the given email.

        Production approves via Clerk's allowlist API + a DB write;
        we skip the Clerk side (no allowlist to enforce in the test
        stack) and write the row directly. On the user's first sign-in
        the e2e user-dependency redeems the row and gifts
        ``starter_credits`` of ``starter_tier``.
        """
        now = utcnow()
        with session_scope() as db:
            # Drop any existing row for this email so re-running a spec
            # doesn't trip the UNIQUE constraint on email. Cases that
            # need to test the "already redeemed" path can submit
            # through the public /api/invite first.
            existing = (
                db.query(InviteRequest)
                .filter(InviteRequest.email.ilike(body.email))
                .all()
            )
            for row in existing:
                db.delete(row)
            db.flush()
            invite = InviteRequest(
                id=f"e2e_{uuid.uuid4().hex[:12]}",
                email=body.email.lower(),
                name=body.name,
                status="approved",
                created_at=now,
                decided_at=now,
                decided_by="e2e-test",
                expires_at=None,
                granted_starter_credits=body.starter_credits,
                granted_starter_tier=body.starter_tier
                if body.starter_credits > 0
                else None,
            )
            db.add(invite)
            db.commit()
            return {
                "invite": {
                    "id": invite.id,
                    "email": invite.email,
                    "status": invite.status,
                    "starter_credits": invite.granted_starter_credits,
                    "starter_tier": invite.granted_starter_tier,
                }
            }

    @app.post("/v1/_test/reset-user")
    async def reset_user(body: _ResetUserBody) -> dict[str, object]:
        """Delete an ``advisor_user`` row + all dependent rows.

        Lets specs that mint a fresh user-id (timestamp-based) clean up
        their footprint if needed. Idempotent — missing user returns
        ``deleted=False`` rather than 404.
        """
        with session_scope() as db:
            user = (
                db.query(User)
                .filter(User.clerk_user_id == body.clerk_user_id)
                .one_or_none()
            )
            if user is None:
                return {"deleted": False}
            # advisor_user.id has ON DELETE CASCADE FKs from every
            # dependent table (cases, chat_sessions, credits, usage
            # events, purchases) — deleting the user row at the DB
            # layer cascades automatically. invite_request matches by
            # email rather than FK, so clean that up alongside.
            email = user.email
            db.delete(user)
            if email:
                db.query(InviteRequest).filter(
                    InviteRequest.email.ilike(email)
                ).delete(synchronize_session=False)
            db.commit()
            return {"deleted": True}

    @app.post("/v1/_test/evaluate-bylaws")
    async def evaluate_bylaws(body: _EvaluateBylawsBody) -> dict[str, object]:
        """Run the compliance evaluator against the configured corpus.

        Mirrors the contract of the ``evaluate_submission_against_bylaws``
        MCP tool — exercised here over HTTP so the Playwright spec can
        seed Postgres, hit the real proxy, and assert on the structured
        response without standing up the full chat-loop machinery.

        ``location`` accepts the same ``LocationSlot`` shape used by
        ``search_bylaw_evidence``. ``persist_decision`` defaults to
        False so the test endpoint stays side-effect-free unless the
        caller asks otherwise.
        """
        with session_scope() as session:
            retrieval = RetrievalService(session)
            evaluator = EvaluatorService(session, retrieval_service=retrieval)
            request = EvaluationRequest(
                attributes=[
                    SubmissionAttributeInput(
                        attribute_key=attr.attribute_key,
                        value=attr.value,
                        unit=attr.unit,
                        source=SubmissionAttributeSource(attr.source),
                        confidence=attr.confidence,
                    )
                    for attr in body.attributes
                ],
                location=(
                    LocationSlot.model_validate(body.location)
                    if body.location
                    else None
                ),
                document_filters=(
                    DocumentFilters(
                        municipality=body.document_filters.get("municipality"),
                        bylaw_name=body.document_filters.get("bylaw_name"),
                        citation_path_prefix=body.document_filters.get(
                            "citation_path_prefix"
                        ),
                        document_id=body.document_filters.get("document_id"),
                    )
                    if body.document_filters
                    else None
                ),
                per_attribute_limit=body.per_attribute_limit,
                submission_id=body.submission_id,
                persist_decision=body.persist_decision,
            )
            response = evaluator.evaluate(request)
            return response.to_json()

    @app.post("/v1/_test/manifest-ingest")
    async def manifest_ingest(body: _ManifestIngestBody) -> dict[str, object]:
        """Drive Layer 1 ingest from a manifest path and return a summary.

        Used by the ABS-74 Playwright spec to prove the agentic loop is
        closed: a CityIntakeManifest on disk + the manifest_adapter is
        enough to ingest a bylaw with no hand-edited ParsingProfile, and
        semantic enrichment honors the manifest's zone codes / use map via
        the ContextVar overlay.

        Idempotent on ``bylaw_name`` — any prior document with the same name
        is deleted before re-ingesting, so the spec can run repeatedly
        against a long-lived test DB without piling up rows.
        """
        manifest_file = Path(body.manifest_path)
        bylaw_file = Path(body.bylaw_path)
        if not manifest_file.exists():
            raise HTTPException(
                status_code=400,
                detail=f"manifest_path not found: {body.manifest_path}",
            )
        if not bylaw_file.exists():
            raise HTTPException(
                status_code=400,
                detail=f"bylaw_path not found: {body.bylaw_path}",
            )

        manifest = load_manifest(manifest_file)
        try:
            profile = profile_from_manifest(manifest, base=HALIFAX_PROFILE)
        except ManifestNotReadyError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

        with session_scope() as session:
            # Drop prior runs with the same bylaw_name so the test is
            # idempotent across runs against a shared test DB.
            session.query(Document).filter(Document.bylaw_name == body.bylaw_name).delete(
                synchronize_session=False
            )
            session.flush()

            document, run = ingest_file(
                session,
                bylaw_file,
                municipality=manifest.municipality.name,
                bylaw_name=body.bylaw_name,
                profile=profile,
            )
            if run.status == IngestionStatus.FAILED:
                return {
                    "ok": False,
                    "errors": list(run.errors_json or []),
                    "warnings": list(run.warnings_json or []),
                }
            enrich_report = enrich_document_semantics(
                session, document_id=document.id, profile=profile
            )
            fragment_count = (
                session.query(SourceFragment)
                .filter_by(document_id=document.id)
                .count()
            )
            citation_paths = [
                row[0]
                for row in (
                    session.query(SourceFragment.citation_path)
                    .filter(
                        SourceFragment.document_id == document.id,
                        SourceFragment.citation_path.isnot(None),
                        SourceFragment.parse_status == ParseStatus.PARSED,
                    )
                    .order_by(SourceFragment.id)
                    .limit(8)
                    .all()
                )
            ]
            zone_entities = [
                row[0]
                for row in (
                    session.query(SemanticEntity.canonical_name)
                    .filter(
                        SemanticEntity.document_id == document.id,
                        SemanticEntity.entity_type == "zone",
                    )
                    .order_by(SemanticEntity.canonical_name)
                    .all()
                )
            ]
            return {
                "ok": True,
                "document_id": document.id,
                "municipality": document.municipality,
                "bylaw_name": document.bylaw_name,
                "parser_version": document.parser_version,
                "jurisdiction_code": profile.jurisdiction_code,
                "fragment_count": fragment_count,
                "citation_paths": citation_paths,
                "zone_entities": zone_entities,
                "manifest_zone_codes": (
                    sorted(profile.known_zone_codes)
                    if profile.known_zone_codes
                    else []
                ),
                "enrichment": enrich_report.model_dump(),
            }

    @app.post("/v1/_test/discover")
    async def discover(body: _DiscoveryBody) -> dict[str, object]:
        """Run the discovery agent's classifier + assembly path.

        Inputs:
            * ``candidates`` — pre-crawled documents (the crawler half is
              already covered by abs-learning unit tests against an
              httpx.MockTransport).
            * ``classifier_responses`` — one entry per batch the agent
              will make; each entry is the ``documents`` array the
              classifier tool returns. Length must match the number of
              batches implied by ``len(candidates) / batch_size``.
            * ``parent_links`` — optional; if present, used as the
              parent-resolution tool response.

        Returns the assembled ``SourceDocument`` list as JSON. The
        spec asserts on shape — primary bylaw identifiable,
        companion list non-empty, news pages dropped.
        """
        try:
            from agents.discovery import (  # type: ignore[import-not-found]
                CLASSIFIER_TOOL_NAME,
                PARENT_RESOLVER_TOOL_NAME,
                CrawledCandidate,
                DiscoveryAgent,
                companions_from_sources,
            )
            from manifest.models import Municipality  # type: ignore[import-not-found]
        except ImportError as exc:
            raise HTTPException(
                status_code=500,
                detail=(
                    f"abs-learning package not importable: {exc}. "
                    "Expected at <repo>/abs-learning/src on sys.path."
                ),
            ) from exc

        def _tool_block(payload: dict[str, Any], tool_name: str) -> MagicMock:
            block = MagicMock()
            block.type = "tool_use"
            block.name = tool_name
            block.input = payload
            response = MagicMock()
            response.content = [block]
            return response

        canned_messages = [
            _tool_block(
                {"documents": list(batch)}, CLASSIFIER_TOOL_NAME
            )
            for batch in body.classifier_responses
        ]
        if body.parent_links is not None:
            canned_messages.append(
                _tool_block(
                    {"links": list(body.parent_links)},
                    PARENT_RESOLVER_TOOL_NAME,
                )
            )

        message_iter = iter(canned_messages)

        def fake_create(*_args, **_kwargs):
            try:
                return next(message_iter)
            except StopIteration as exc:  # pragma: no cover — caller bug
                raise HTTPException(
                    status_code=400,
                    detail=(
                        "Discovery test endpoint: ran out of canned LLM "
                        "responses. Provide one per classifier batch "
                        "plus one parent-resolution call if needed."
                    ),
                ) from exc

        fake_llm = MagicMock()
        fake_llm.messages.create.side_effect = fake_create

        # The agent never makes HTTP requests in this code path
        # (no _crawl call), but it still needs an http_client to
        # satisfy the constructor.
        import httpx

        agent = DiscoveryAgent(
            fake_llm,
            http_client=httpx.Client(timeout=1.0),
            confidence_floor=body.confidence_floor,
            batch_size=body.batch_size,
        )
        try:
            municipality = Municipality(
                name=body.municipality.name,
                jurisdiction_code=body.municipality.jurisdiction_code,
                province=body.municipality.province,
                governing_body=body.municipality.governing_body,
            )
            candidates = [
                CrawledCandidate(
                    url=c.url,
                    link_text=c.link_text,
                    discovered_on=c.discovered_on,
                    page_title=c.page_title,
                    content_type=c.content_type,
                    is_pdf=c.is_pdf,
                )
                for c in body.candidates
            ]
            classified = agent._classify(candidates, municipality)
            classified = agent._resolve_parents(classified, municipality)
            sources = agent._to_source_documents(classified)
        finally:
            agent.close()

        return {
            "ok": True,
            "sources": [s.model_dump(mode="json") for s in sources],
            "companions": companions_from_sources(sources),
            "llm_call_count": fake_llm.messages.create.call_count,
        }


app = build_e2e_app()


if __name__ == "__main__":  # pragma: no cover
    import uvicorn

    uvicorn.run(
        "advisor.api.e2e_server:app",
        host=os.environ.get("ADVISOR_HOST", "127.0.0.1"),
        port=int(os.environ.get("ADVISOR_PORT", "8001")),
        reload=False,
    )
