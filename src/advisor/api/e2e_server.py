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
from advisor.api.metrics_middleware import MetricsMiddleware
from advisor.db.models import InviteRequest, User
from advisor.llm.mock import MockGateway
from advisor.logging import CorrelationIdMiddleware, setup_logging
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
from layer1.pipeline.verify_coverage import compare_coverage_reports, verify_document_coverage
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
    setup_logging(json_output=False)
    gateway = MockGateway(callable_=build_dispatcher())

    # ABS-19: wire a real ClerkVerifier backed by an in-memory test RSA
    # key so the e2e suite exercises the full JWT verification pipeline
    # (ClerkVerifier → clerk_session_dependency → resolve_or_create_user).
    # test_header_fallback=True keeps the X-Test-User-Id header path
    # working for direct FastAPI calls from Playwright fixtures.
    from advisor.auth.mock_clerk import build_mock_verifier  # noqa: PLC0415

    mock_verifier = build_mock_verifier()

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
        verifier=mock_verifier,
        db_session_factory=session_scope,
        submissions_evaluator_factory=_submissions_evaluator_factory,
        test_header_fallback=True,
    )
    app.add_middleware(CorrelationIdMiddleware)
    app.add_middleware(MetricsMiddleware)

    from advisor.api.metrics import mount_metrics_routes  # noqa: PLC0415

    mount_metrics_routes(app)

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
            "X-Correlation-ID",
            "X-ABS-API-Key",
        ],
        expose_headers=["X-Session-Id", "X-Correlation-ID"],
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


# ABS-273: test-only address-profile endpoint. Bypasses the chat / SSE
# pipeline so the Playwright spec can assert directly on the AddressProfile
# the get_address_profile MCP tool returns, exercised over real-stack HTTP +
# Postgres/PostGIS so a missing migration, geometry-column gap, or proxy
# misconfig trips e2e rather than staying invisible until production.
class _AddressProfileBody(BaseModel):
    address: str = Field(min_length=1, max_length=512)


# ABS-163: test-only table-search endpoint. Verifies that SourceTable rows
# with proper captions are found by the retrieval layer's
# _structured_permission_table_candidates() path. The Playwright spec seeds
# tables via seed_e2e_permission_tables.py, then hits this endpoint to
# confirm the query finds them by caption pattern.
class _SearchTablesBody(BaseModel):
    bylaw_name: str = Field(min_length=1, max_length=256)
    use_name: str = Field(min_length=1, max_length=256)
    zone: str | None = None


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


class _IssueApiKeyBody(BaseModel):
    clerk_user_id: str = Field(min_length=1, max_length=255)
    name: str = Field(default="e2e-test-key", min_length=1, max_length=255)


class _MintJwtBody(BaseModel):
    sub: str = Field(min_length=1, max_length=255)
    email: str | None = None
    full_name: str | None = None
    lifetime_s: int = Field(default=3600, ge=60, le=86400)


class _VerifyCoverageBody(BaseModel):
    document_id: int
    low_coverage_threshold: float = Field(default=0.3, ge=0.0, le=1.0)
    compare_to: int | None = None


# ABS-213: test-only prune-superseded endpoint.
# Seeds synthetic Document rows for a named (municipality, bylaw_name) pair,
# then exercises prune_superseded_documents so the Playwright spec can verify
# dry-run scan, live delete, and municipal-filter targeting through the real
# Postgres stack without touching production data.
class _PruneSupersededBody(BaseModel):
    bylaw_name: str = Field(min_length=1, max_length=256)
    municipality: str = Field(default="Test Municipality ABS-213", min_length=1, max_length=255)
    doc_count: int = Field(default=3, ge=1, le=10)
    keep_latest: int = Field(default=1, ge=1, le=9)
    dry_run: bool = True


def _mount_test_router(app: FastAPI) -> None:
    """Wire the ``/v1/_test/...`` lifecycle endpoints onto ``app``."""

    @app.post("/v1/_test/mint-jwt")
    async def mint_jwt(body: _MintJwtBody) -> dict[str, str]:
        """Mint a test JWT signed by the e2e mock RSA key.

        The returned token is accepted by the ``ClerkVerifier`` wired
        into this e2e server. Playwright specs use this to get a JWT
        they can set as a cookie, which the Next.js Clerk mock reads
        and forwards as ``Authorization: Bearer <jwt>`` to FastAPI.
        """
        from advisor.auth.mock_clerk import mint_test_jwt  # noqa: PLC0415

        token = mint_test_jwt(
            sub=body.sub,
            email=body.email,
            full_name=body.full_name,
            lifetime_s=body.lifetime_s,
        )
        return {"token": token}

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

    @app.post("/v1/_test/address-profile")
    async def address_profile(body: _AddressProfileBody) -> dict[str, object]:
        """Resolve an address to its zone + overlay profile.

        Mirrors the ``get_address_profile`` MCP tool over HTTP so the
        Playwright spec can seed Postgres, hit the real proxy, and assert on
        the structured ``AddressProfile`` (zone, height/FAR precincts,
        heritage, citations) without standing up the chat-loop machinery.
        Returns the full DTO (not the compact projection) so the spec can
        assert on every field.
        """
        with session_scope() as session:
            service = RetrievalService(session)
            return service.get_address_profile(body.address).model_dump(mode="json")

    @app.post("/v1/_test/search-tables")
    async def search_tables(body: _SearchTablesBody) -> dict[str, object]:
        """Search for permission-table cells matching a use name.

        Returns the table captions and matching cell texts found by the
        retrieval layer's ``_structured_permission_table_candidates()``.
        """
        from sqlalchemy import select as sa_select

        from layer1.db.base import SourceTable, SourceTableCell
        from layer2.retrieval.api import _structured_permission_table_candidates

        with session_scope() as db:
            doc = db.execute(
                sa_select(Document).where(Document.bylaw_name == body.bylaw_name)
            ).scalars().first()
            if doc is None:
                raise HTTPException(status_code=404, detail=f"No document named '{body.bylaw_name}'")

            candidates = _structured_permission_table_candidates(
                db,
                document_id=doc.id,
                use_name=body.use_name,
                zone=body.zone,
            )

            tables = (
                db.execute(
                    sa_select(SourceTable)
                    .where(SourceTable.document_id == doc.id)
                    .where(SourceTable.caption.ilike("Table 1%Permitted uses by zone%"))
                )
                .scalars()
                .all()
            )

            return {
                "document_id": doc.id,
                "table_count": len(tables),
                "table_captions": [t.caption for t in tables],
                "candidate_count": len(candidates),
                "candidates": [
                    {"text": c.text, "score": c.base_score, "citation_label": c.citation_label}
                    for c in candidates
                ],
            }

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

    @app.post("/v1/_test/issue-api-key")
    async def issue_api_key(body: _IssueApiKeyBody) -> dict[str, object]:
        """Create a fresh API key for the given user (test-only).

        Returns the raw key — the only time it is visible. The spec must
        capture it immediately. Callers must first create the user via
        ``/v1/_test/invite-approve`` + a first-login request so the
        ``advisor_user`` row exists.
        """
        from advisor.api.api_key_auth import issue_api_key as _issue  # noqa: PLC0415

        with session_scope() as db:
            user = (
                db.query(User)
                .filter(User.clerk_user_id == body.clerk_user_id)
                .one_or_none()
            )
            if user is None:
                raise HTTPException(
                    status_code=404,
                    detail=f"No user with clerk_user_id={body.clerk_user_id!r}.",
                )
            row, raw_key = _issue(db, user_id=user.id, name=body.name)
            db.commit()
            return {
                "api_key_id": row.id,
                "raw_key": raw_key,
                "name": row.name,
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

    @app.post("/v1/_test/prune-superseded")
    async def prune_superseded_test(body: _PruneSupersededBody) -> dict[str, object]:
        """Seed synthetic Document rows then exercise prune_superseded_documents.

        Creates ``doc_count`` Document rows for (municipality, bylaw_name) with
        staggered ``ingestion_timestamp`` values (1-second apart, oldest first),
        runs prune_superseded_documents with the given ``keep_latest`` / ``dry_run``
        settings, and returns the full PruneResult summary.

        Idempotent: all Document rows for (municipality, bylaw_name) are dropped
        before seeding so repeated spec runs against a shared DB stay clean.
        """
        import hashlib
        from datetime import timedelta

        from layer1.db.base import Document as _Document, IngestionRun as _IngestionRun
        from layer1.models.enums import IngestionStatus
        from layer1.pipeline.prune import prune_superseded_documents

        with session_scope() as db:
            # Clean up any prior rows from a previous spec run.
            db.query(_Document).filter(
                _Document.municipality == body.municipality,
                _Document.bylaw_name == body.bylaw_name,
            ).delete(synchronize_session=False)
            db.flush()

            # Seed `doc_count` synthetic Document rows with staggered timestamps
            # (oldest first, 1 second apart) so prune ordering is deterministic.
            base_time = utcnow()
            for i in range(body.doc_count):
                ts = base_time - timedelta(seconds=(body.doc_count - 1 - i))
                file_hash = hashlib.sha256(
                    f"{body.bylaw_name}-{body.municipality}-{i}".encode()
                ).hexdigest()[:64]
                doc = _Document(
                    municipality=body.municipality,
                    bylaw_name=body.bylaw_name,
                    source_path=f"/tmp/abs213-test-{i}.txt",
                    file_hash=file_hash,
                    mime_type="text/plain",
                    page_count=1,
                    ingestion_timestamp=ts,
                )
                db.add(doc)
                db.flush()
                run = _IngestionRun(
                    document_id=doc.id,
                    status=IngestionStatus.COMPLETED,
                )
                db.add(run)
            db.flush()

            result = prune_superseded_documents(
                db,
                municipality=body.municipality,
                bylaw_name=body.bylaw_name,
                keep_latest=body.keep_latest,
                dry_run=body.dry_run,
            )
            return {
                "dry_run": result.dry_run,
                "entries_count": len(result.entries),
                "deleted_count": result.deleted_count,
                "entries": [
                    {
                        "id": e.id,
                        "municipality": e.municipality,
                        "bylaw_name": e.bylaw_name,
                        "run_count": e.run_count,
                    }
                    for e in result.entries
                ],
            }

    @app.post("/v1/_test/verify-coverage")
    async def verify_coverage(body: _VerifyCoverageBody) -> dict[str, object]:
        """Run ingest coverage verification for a document.

        Returns the structured DocumentCoverageReport with per-page
        overlap ratios, gap classifications, and a letter grade.
        """
        with session_scope() as db:
            doc = db.get(Document, body.document_id)
            if doc is None:
                raise HTTPException(
                    status_code=404,
                    detail=f"Document {body.document_id} not found",
                )
            report = verify_document_coverage(
                db,
                body.document_id,
                low_coverage_threshold=body.low_coverage_threshold,
            )
            if body.compare_to is not None:
                old_doc = db.get(Document, body.compare_to)
                if old_doc is None:
                    raise HTTPException(
                        status_code=404,
                        detail=f"compare_to document {body.compare_to} not found",
                    )
                old_report = verify_document_coverage(
                    db,
                    body.compare_to,
                    low_coverage_threshold=body.low_coverage_threshold,
                )
                report.comparison = compare_coverage_reports(old_report, report)
            return report.model_dump(mode="json")

    @app.post("/v1/_test/lookup-citation")
    async def test_lookup_citation(body: dict) -> dict[str, object]:
        """Invoke ``lookup_citation`` directly against the e2e test DB.

        Accepts the same JSON body as ``CitationLookupRequest``:
        - ``{"citation_path": "4.2"}``
        - ``{"structured": {"kind": "zone_attribute", "zone": "HR-2", "attribute": "max_height"}}``
        - ``{"structured": {"kind": "schedule_row", "schedule": "Table 1A", "row": "HR-2"}}``

        Returns a ``CitationLookupResponse`` dict (``match`` + ``suggestions``).
        Returns 422 on ``ValidationError`` (unknown attribute, missing required
        field) and 400 on ``ValueError`` (ambiguous across documents).
        Scoped with no default document resolver so the full test corpus is
        visible — tests can narrow scope via ``document_id`` in the body.
        """
        from pydantic import ValidationError  # noqa: PLC0415
        from bylaw_retrieval.retrieval import CitationLookupRequest  # noqa: PLC0415

        try:
            request = CitationLookupRequest.model_validate(body)
        except ValidationError as exc:
            raise HTTPException(status_code=422, detail=exc.errors()) from exc

        try:
            with session_scope() as session:
                service = RetrievalService(session)
                response = service.lookup_citation(request)
                return response.model_dump(mode="json")
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    class _DeleteDocumentsBody(BaseModel):
        bylaw_name: str = Field(min_length=1, max_length=512)

    @app.post("/v1/_test/delete-documents")
    async def delete_documents(body: _DeleteDocumentsBody) -> dict[str, object]:
        """Delete all Document rows with the given bylaw_name.

        Used by e2e specs that ingest a synthetic document during the test
        and need to remove it afterwards so it does not pollute the
        lookup_citation scope for concurrently-running specs.
        """
        with session_scope() as db:
            deleted = (
                db.query(Document)
                .filter(Document.bylaw_name == body.bylaw_name)
                .delete(synchronize_session=False)
            )
            return {"deleted_count": deleted}


class _SeedSessionBody(BaseModel):
    """Body for ``POST /v1/_test/seed-session``.

    Inserts a ChatSession with synthetic tool_use + tool_result messages
    that include ``linked_datasets`` and ``citation_path`` values. The
    session simulates a completed search_bylaw_evidence round so the
    right-pane ``extractParcelContext`` can reconstruct parcel + citation
    data without requiring real bylaw content in the test DB.
    """

    case_id: int
    user_id: str = "demo-user-1"
    civic_number: str = "1234"
    street: str = "Elm St"
    citation_path: str = "4.2.1"
    citation_label: str = "(1)"
    bylaw_name: str = "Regional Centre Land Use By-Law"
    clause_text: str = (
        "The minimum front yard setback shall be 3.0 metres from the property line."
    )


def _mount_seed_session_endpoint(app: FastAPI) -> None:
    @app.post("/v1/_test/seed-session")
    async def seed_session(body: _SeedSessionBody) -> dict[str, object]:
        """Seed a ChatSession with citation-bearing tool results.

        Creates a DB-backed session for the given case so ``GET
        /v1/chat/sessions/{id}`` returns messages that cause
        ``extractParcelContext`` to populate the parcel pane with
        ``cited`` entries — without requiring real bylaw text in the DB.
        """
        import json as _json

        from advisor.db.models import (  # noqa: PLC0415
            ChatMessage as _DbChatMessage,
            ChatSession as _DbChatSession,
            User as _User,
        )

        tool_result_payload = _json.dumps({
            "matches": [
                {
                    "citation_path": body.citation_path,
                    "citation_label": body.citation_label,
                    "bylaw_name": body.bylaw_name,
                    "fragment_type": "clause",
                    "linked_datasets": [
                        {
                            "name": "halifax_zoning_boundaries",
                            "location_resolver": "civic_number",
                            "location_confidence": 0.92,
                            "feature_matches": [
                                {
                                    "canonical_attributes": {
                                        "zone_code": "C-2",
                                        "zone_description": "General Commercial",
                                    },
                                }
                            ],
                        }
                    ],
                }
            ]
        })

        with session_scope() as db:
            user_row = (
                db.query(_User)
                .filter(_User.clerk_user_id == body.user_id)
                .first()
            )
            if user_row is None:
                raise HTTPException(
                    status_code=404,
                    detail=f"User '{body.user_id}' not found",
                )

            session_row = _DbChatSession(
                user_id=user_row.id,
                case_id=body.case_id,
                tier="standard",
                updated_at=utcnow(),
            )
            db.add(session_row)
            db.flush()

            tool_use_id = f"t-seed-{session_row.id}"
            messages = [
                {
                    "sequence": 0,
                    "role": "user",
                    "content_json": (
                        f"What is the front yard setback for "
                        f"{body.civic_number} {body.street}?"
                    ),
                },
                {
                    "sequence": 1,
                    "role": "assistant",
                    "content_json": [
                        {"type": "text", "text": "Searching the bylaw…", "cache": False},
                        {
                            "type": "tool_use",
                            "id": tool_use_id,
                            "name": "search_bylaw_evidence",
                            "input": {
                                "query": "front yard setback",
                                "top_k": 4,
                                "location": {
                                    "civic_number": body.civic_number,
                                    "street": body.street,
                                },
                            },
                            "cache": False,
                        },
                    ],
                },
                {
                    "sequence": 2,
                    "role": "user",
                    "content_json": [
                        {
                            "type": "tool_result",
                            "tool_use_id": tool_use_id,
                            "content": tool_result_payload,
                            "is_error": False,
                            "cache": False,
                        }
                    ],
                },
                {
                    "sequence": 3,
                    "role": "assistant",
                    "content_json": (
                        "Based on the bylaw evidence, the front yard setback is 3 m."
                    ),
                },
            ]
            for m in messages:
                db.add(
                    _DbChatMessage(
                        session_id=session_row.id,
                        sequence=m["sequence"],
                        role=m["role"],
                        content_json=m["content_json"],
                    )
                )
            db.flush()
            return {"session_id": str(session_row.id)}


class _SearchEvidenceBody(BaseModel):
    query: str
    bylaw_name: str | None = None
    municipality: str | None = None
    limit: int = Field(default=5, ge=1, le=50)


def _mount_search_evidence_endpoint(app: FastAPI) -> None:
    """ABS-271: expose search_bylaw_evidence over HTTP for e2e limit tests.

    Accepts ``query`` + optional ``bylaw_name`` / ``municipality`` scope and
    a ``limit`` parameter (1–50). Returns the fragment IDs and scores in
    ranked order so Playwright can assert on count and ordering invariants.
    """
    from bylaw_retrieval.retrieval import RetrievalRequest, RetrievalService  # noqa: PLC0415

    @app.post("/v1/_test/search-evidence")
    async def search_evidence(body: _SearchEvidenceBody) -> dict[str, object]:
        with session_scope() as session:
            service = RetrievalService(session)
            request = RetrievalRequest(
                query=body.query,
                bylaw_name=body.bylaw_name,
                municipality=body.municipality,
                limit=body.limit,
                include_context=False,
                include_cross_references=False,
                include_tables=False,
                include_datasets=False,
            )
            response = service.search(request)
            return {
                "total_matches": response.total_matches,
                "match_count": len(response.matches),
                "fragment_ids": [m.fragment_id for m in response.matches],
                "scores": [m.score for m in response.matches],
            }


app = build_e2e_app()
_mount_seed_session_endpoint(app)
_mount_search_evidence_endpoint(app)


if __name__ == "__main__":  # pragma: no cover
    import uvicorn

    uvicorn.run(
        "advisor.api.e2e_server:app",
        host=os.environ.get("ADVISOR_HOST", "127.0.0.1"),
        port=int(os.environ.get("ADVISOR_PORT", "8001")),
        reload=False,
    )
