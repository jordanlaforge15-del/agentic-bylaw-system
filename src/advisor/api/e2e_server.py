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

# ABS-430 (with ABS-428): the e2e entrypoint — including every
# /v1/_test/* seed endpoint it mounts — may only ever address a test
# database. Mirror the seed-script bootstrap (scripts/e2e_db_default):
# default DATABASE_URL to the dedicated e2e instance, then hard-refuse
# any effective target whose database name does not end in `_test`,
# unless E2E_SEED_ALLOW_DB=<exact-db-name> explicitly whitelists it.
# Must run before the advisor/layer1 imports below so the lru_cached
# layer1 settings can only ever resolve to the guarded URL.
from layer1.seed_guard import default_e2e_database_url, require_test_database

os.environ.setdefault("DATABASE_URL", default_e2e_database_url())
require_test_database()

from advisor.api.app import create_app
from advisor.api.metrics_middleware import MetricsMiddleware
from advisor.db.models import InviteRequest, User
from advisor.llm.mock import MockGateway
from advisor.logging import CorrelationIdMiddleware, setup_logging
from advisor.llm.mock_dispatcher import build_dispatcher
from bylaw_retrieval.retrieval import CitationLookupRequest, LocationSlot, RetrievalService
from layer1.db.base import Document, SemanticEntity, SourceFragment, utcnow
from layer1.db.session import session_scope
from layer1.manifest_adapter import (
    ManifestNotReadyError,
    load_manifest,
    profile_from_manifest,
)
from layer1.models.enums import FragmentType, IngestionStatus, ParseStatus
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
    # ABS-432: this entrypoint's database IS the e2e suite's own instance —
    # the `e2e-seed` documents and `e2e_*` datasets the seed scripts create
    # are fixtures, not contamination. Declare that to the monitoring
    # router's e2e_contamination tripwire so /v1/monitoring/corpus-coherence
    # reports them informationally instead of going red. Production
    # (advisor.api.main) and dev (advisor.api.dev) never import this module,
    # so any marker row there still flips monitoring to 503.
    os.environ.setdefault("ADVISOR_E2E_MARKERS_EXPECTED", "1")
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
# tables via seed_e2e_rclub_unified.py (ABS-433), then hits this endpoint to
# confirm the query finds them by caption pattern.
class _SearchTablesBody(BaseModel):
    bylaw_name: str = Field(min_length=1, max_length=256)
    use_name: str = Field(min_length=1, max_length=256)
    zone: str | None = None


# ABS-278: test-only endpoint that runs semantic enrichment for a bylaw and
# returns the resulting permission-matrix profiles + bound axes, plus an
# optional (use, zone) -> cell resolution. The Playwright spec seeds a matrix
# (including a header-bleed cell), hits this, and asserts axes are bound to zone/
# use entities and the bleed is corrected.
class _LinkTableCaptionsBody(BaseModel):
    """Body for ``POST /v1/_test/link-table-captions`` (ABS-409)."""

    bylaw_name: str = Field(min_length=1, max_length=512)
    profile: str = "halifax"
    dry_run: bool = False


class _GeometryConsistencyBody(BaseModel):
    """Body for ``POST /v1/_test/geometry-consistency`` (ABS-491).

    Optional scope: omit ``dataset_id`` to audit every ingested feature in
    the database, which is what a spec asserting "no seed forgot the
    writer" wants.
    """

    dataset_id: int | None = None


class _ProfilePermissionTablesBody(BaseModel):
    bylaw_name: str = Field(min_length=1, max_length=256)
    use_name: str | None = Field(default=None, max_length=256)
    zone: str | None = Field(default=None, max_length=64)
    # ABS-284: drive enrichment classification from a profile convention. When
    # set (e.g. "section_indexed"), the bylaw's permission encoding is supplied
    # to enrichment so its table classification is profile-driven rather than
    # the hardcoded Regional-Centre default.
    permission_encoding: str | None = Field(default=None, max_length=64)


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


class _CorpusCoherenceOverlayDeclaration(BaseModel):
    dataset_name: str
    municipality: str
    bylaw_name: str
    fragment_citation: str


class _CorpusCoherenceBody(BaseModel):
    overlay_declarations: list[_CorpusCoherenceOverlayDeclaration] = Field(
        default_factory=list,
        description=(
            "The overlay roles a spec's seed script declares (test-scoped, "
            "not the real src/layer1/datasets/ configs, so this endpoint "
            "never expects the beta-hardening real corpus's bonus_zoning / "
            "shadow_impact overlays that an isolated e2e bylaw never seeds)."
        ),
    )


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

    @app.post("/v1/_test/address-profile-scoped")
    async def address_profile_scoped(body: _AddressProfileBody) -> dict[str, object]:
        """Resolve an address under the *production* enabled-documents scoping.

        The plain ``/address-profile`` endpoint deliberately runs with no
        default-document resolver so it sees the full corpus regardless of
        same-name collisions in the shared e2e DB (the ABS-349/350 concern).
        This variant instead wires ``retrieval_enabled_resolver`` — exactly
        what ``advisor.api.app`` and the MCP ``server`` use in production — so
        the ABS-355 spec reproduces the real amendment eviction: a layer still
        pinned to a disabled document version falls out of scope and the
        zone resolves to null. With publish-driven re-linking in place the
        layer follows the amendment onto the newly enabled version and the
        zone resolves again.
        """
        from bylaw_retrieval.retrieval import retrieval_enabled_resolver  # noqa: PLC0415

        with session_scope() as session:
            service = RetrievalService(
                session, default_document_id_resolver=retrieval_enabled_resolver
            )
            return service.get_address_profile(body.address).model_dump(mode="json")

    @app.post("/v1/_test/search-tables")
    async def search_tables(body: _SearchTablesBody) -> dict[str, object]:
        """Search for permission-table cells matching a use name.

        Returns the table captions and matching cell texts found by the
        retrieval layer's ``_structured_permission_table_candidates()``.
        """
        from sqlalchemy import select as sa_select

        from layer1.db.base import SourceTable, SourceTableCell, TableSemanticProfile
        from layer1.semantic.permission_markers import PERMISSION_MATRIX_PROFILE
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
                    .join(
                        TableSemanticProfile,
                        TableSemanticProfile.table_id == SourceTable.id,
                    )
                    .where(SourceTable.document_id == doc.id)
                    .where(
                        TableSemanticProfile.profile_type == PERMISSION_MATRIX_PROFILE
                    )
                )
                .scalars()
                .all()
            )

            # ABS-277: surface the recovered permission_marker on each cell so
            # the Playwright spec can assert the symbol-font ● (U+F098) was
            # normalized to "permitted", circled numbers to "conditional", etc.
            cells_out: list[dict[str, object]] = []
            for table in tables:
                table_cells = (
                    db.execute(
                        sa_select(SourceTableCell)
                        .where(SourceTableCell.table_id == table.id)
                        .order_by(
                            SourceTableCell.row_index, SourceTableCell.col_index
                        )
                    )
                    .scalars()
                    .all()
                )
                for cell in table_cells:
                    meta = cell.metadata_json or {}
                    cells_out.append(
                        {
                            "table_id": table.id,
                            "row_index": cell.row_index,
                            "col_index": cell.col_index,
                            "row_header_path": cell.row_header_path,
                            "col_header_path": cell.col_header_path,
                            "permission_marker": meta.get("permission_marker"),
                            "footnote": meta.get("footnote"),
                        }
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
                "cells": cells_out,
            }

    @app.post("/v1/_test/profile-permission-tables")
    async def profile_permission_tables(
        body: _ProfilePermissionTablesBody,
    ) -> dict[str, object]:
        """Enrich a bylaw and return its permission-matrix profiles + axes.

        ABS-278: proves the axes are bound to zone/use entities, that the
        header-bleed correction fires, and that a (use, zone) pair resolves to
        the addressed cell.
        """
        from sqlalchemy import select as sa_select

        from layer1.db.base import (
            SemanticEntity,
            SourceTable,
            TableAxisBinding,
            TableSemanticProfile,
        )
        from layer1.semantic.enrichment import (
            enrich_document_semantics,
            resolve_permission_cell,
        )
        from layer1.semantic.permission_markers import PERMISSION_MATRIX_PROFILE

        with session_scope() as db:
            doc = db.execute(
                sa_select(Document).where(Document.bylaw_name == body.bylaw_name)
            ).scalars().first()
            if doc is None:
                raise HTTPException(
                    status_code=404, detail=f"No document named '{body.bylaw_name}'"
                )

            # ABS-284: build a minimal profile carrying the requested permission
            # encoding so enrichment classification is profile-driven. None keeps
            # the historical Regional-Centre default behavior.
            profile = None
            if body.permission_encoding:
                from layer1.profiles import ParsingProfile

                profile = ParsingProfile(
                    name=f"e2e:{body.permission_encoding}",
                    permission_encoding=body.permission_encoding,
                )
            enrich_document_semantics(db, document_id=doc.id, profile=profile)

            tables = (
                db.execute(
                    sa_select(SourceTable)
                    .join(
                        TableSemanticProfile,
                        TableSemanticProfile.table_id == SourceTable.id,
                    )
                    .where(SourceTable.document_id == doc.id)
                    .where(
                        TableSemanticProfile.profile_type == PERMISSION_MATRIX_PROFILE
                    )
                    .order_by(SourceTable.page_start, SourceTable.id)
                )
                .scalars()
                .all()
            )

            profiles_out: list[dict[str, object]] = []
            for table in tables:
                profile = (
                    db.execute(
                        sa_select(TableSemanticProfile).where(
                            TableSemanticProfile.table_id == table.id
                        )
                    )
                    .scalars()
                    .first()
                )
                bindings = (
                    db.execute(
                        sa_select(TableAxisBinding, SemanticEntity)
                        .join(
                            SemanticEntity,
                            SemanticEntity.id == TableAxisBinding.entity_id,
                        )
                        .where(TableAxisBinding.table_id == table.id)
                        .order_by(TableAxisBinding.axis, TableAxisBinding.index)
                    )
                    .all()
                )
                profiles_out.append(
                    {
                        "table_id": table.id,
                        "caption": table.caption,
                        "profile_type": profile.profile_type if profile else None,
                        "row_axis_type": profile.row_axis_type if profile else None,
                        "column_axis_type": profile.column_axis_type if profile else None,
                        "value_type": profile.value_type if profile else None,
                        "axes": [
                            {
                                "axis": binding.axis,
                                "index": binding.index,
                                "entity_type": entity.entity_type,
                                "canonical_name": entity.canonical_name,
                                "raw_label": binding.raw_label,
                                "confidence": binding.confidence,
                                "review": (binding.metadata_json or {}).get("review"),
                            }
                            for binding, entity in bindings
                        ],
                    }
                )

            resolution: dict[str, object] | None = None
            if body.use_name and body.zone and tables:
                for table in tables:
                    resolved = resolve_permission_cell(
                        db,
                        table_id=table.id,
                        use_name=body.use_name,
                        zone=body.zone,
                    )
                    if resolved is not None:
                        resolution = resolved
                        break

            return {
                "document_id": doc.id,
                "table_count": len(tables),
                "profiles": profiles_out,
                "resolution": resolution,
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
            # Test-context auto-publish (ABS-413): production ingest leaves a
            # document disabled until an operator enables it, but the ABS-74
            # spec asserts retrieval sees the ingested bylaw immediately.
            document.retrieval_enabled = True
            session.flush()
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

    @app.post("/v1/_test/corpus-coherence")
    async def corpus_coherence(body: _CorpusCoherenceBody) -> dict[str, object]:
        """Run the corpus-coherence audit (ABS-356) against the current DB state.

        Mirrors the CLI (``scripts/corpus_coherence_audit.py``) and the
        ``/v1/monitoring/corpus-coherence`` ops endpoint: scopes with
        ``retrieval_enabled_resolver`` — the same resolver production wires
        into ``RetrievalService`` — so a Playwright spec can seed a coherent
        corpus, assert the audit passes, break one link, and assert it fails
        naming the missing role exactly as a real deployment would see it.
        """
        from bylaw_retrieval.retrieval import (  # noqa: PLC0415
            OverlayDeclaration,
            audit_corpus_coherence,
            retrieval_enabled_resolver,
        )

        declarations = [
            OverlayDeclaration(
                dataset_name=d.dataset_name,
                municipality=d.municipality,
                bylaw_name=d.bylaw_name,
                fragment_citation=d.fragment_citation,
            )
            for d in body.overlay_declarations
        ]
        with session_scope() as session:
            report = audit_corpus_coherence(
                session,
                overlay_declarations=declarations,
                default_document_id_resolver=retrieval_enabled_resolver,
            )
        return report.model_dump(mode="json")

    @app.post("/v1/_test/e2e-contamination")
    async def e2e_contamination() -> dict[str, object]:
        """Run the ABS-432 e2e-contamination sweep, armed as production judges it.

        The ``/v1/monitoring/corpus-coherence`` endpoint in THIS process
        reports markers informationally (``expected_test_fixtures``) because
        the e2e stack's database legitimately holds seeded fixtures — and it
        caches results for 30s, which would race a spec that mutates markers.
        This endpoint returns the raw, uncached ``E2eContaminationReport`` —
        exactly what a dev/prod deployment's tripwire evaluates — so a
        Playwright spec can insert a synthetic marker row, assert the sweep
        names it, delete it, and assert it is gone.
        """
        from bylaw_retrieval.retrieval import audit_e2e_contamination  # noqa: PLC0415

        with session_scope() as session:
            report = audit_e2e_contamination(session)
        return report.model_dump(mode="json")

    @app.post("/v1/_test/geometry-consistency")
    async def geometry_consistency(
        body: _GeometryConsistencyBody | None = None,
    ) -> dict[str, object]:
        """Audit ``external_dataset_feature.geometry`` against its GeoJSON (ABS-491).

        The PostGIS ``geometry`` column is a denormalization of
        ``geometry_geojson`` — the shape every spatial query actually
        matches against, derived from the shape every other read path
        trusts. sqlite unit tests can't see the column at all, so this is
        the only place the two are compared against a real PostGIS: a
        Playwright spec seeds features through the single writer and
        asserts the audit finds zero rows missing, drifted, or in the
        wrong SRID.
        """
        from layer1.db.geometry import audit_feature_geometry  # noqa: PLC0415

        with session_scope() as session:
            report = audit_feature_geometry(
                session,
                dataset_id=body.dataset_id if body else None,
            )
        return report.model_dump(mode="json")

    @app.post("/v1/_test/enabled-name-collisions")
    async def enabled_name_collisions() -> dict[str, object]:
        """Run the ABS-434 enabled-name-collision audit, uncached.

        The ``/v1/monitoring/corpus-coherence`` endpoint carries the same
        check but caches results for 30s, which would race a Playwright spec
        that seeds and then heals a collision. This endpoint returns the raw
        ``EnabledNameCollisionReport`` — at most one retrieval-enabled
        document per case/hyphen/whitespace-normalized ``(municipality,
        bylaw_name)`` — so a spec can seed a case-variant pair ("Test
        By-law" / "Test By-Law"), assert the audit names both ids, disable
        one, and assert the report goes green.
        """
        from bylaw_retrieval.retrieval import (  # noqa: PLC0415
            audit_enabled_name_collisions,
        )

        with session_scope() as session:
            report = audit_enabled_name_collisions(session)
        return report.model_dump(mode="json")

    @app.post("/v1/_test/governing-bylaw-coverage")
    async def governing_bylaw_coverage() -> dict[str, object]:
        """Run the ABS-472 governing-by-law coverage audit, uncached.

        Same reason as the two above: ``/v1/monitoring/corpus-coherence``
        carries this section but caches for 30s, so a spec whose seed adds
        features could read a body assembled before its own ``beforeAll``
        ran. This endpoint answers from the database as it stands.

        Scoped through ``retrieval_enabled_resolver`` so "held" means what it
        means to a real request — visible in the active retrieval scope, not
        merely ingested.
        """
        from bylaw_retrieval.retrieval import (  # noqa: PLC0415
            audit_governing_bylaw_coverage,
            retrieval_enabled_resolver,
        )

        with session_scope() as session:
            report = audit_governing_bylaw_coverage(
                session, default_document_id_resolver=retrieval_enabled_resolver
            )
        return report.model_dump(mode="json")

    @app.post("/v1/_test/link-table-captions")
    async def link_table_captions_endpoint(
        body: _LinkTableCaptionsBody,
    ) -> dict[str, object]:
        """ABS-409: run the table-caption linking pass + caption-aware
        re-enrichment against the newest document with ``bylaw_name`` —
        the same sequence scripts/backfill_table_citations.py applies to a
        real corpus — so the e2e spec can heal the seeded orphan state
        through the shipped code path.
        """
        from layer1.pipeline.table_captions import (  # noqa: PLC0415
            link_table_captions,
        )
        from layer1.semantic.enrichment import (  # noqa: PLC0415
            enrich_document_semantics,
        )

        with session_scope() as session:
            document = (
                session.query(Document)
                .filter(Document.bylaw_name == body.bylaw_name)
                .order_by(Document.id.desc())
                .first()
            )
            if document is None:
                raise HTTPException(status_code=404, detail="bylaw_name not found")
            stats = link_table_captions(
                session,
                document_id=document.id,
                profile=body.profile,
                dry_run=body.dry_run,
            )
            table_profiles = 0
            if not body.dry_run and stats.writes:
                report = enrich_document_semantics(session, document_id=document.id)
                table_profiles = report.table_profiles
            return {
                "document_id": document.id,
                "captions_seen": stats.captions_seen,
                "captions_linked": stats.captions_linked,
                "tables_claimed": stats.tables_claimed,
                "ambiguous_skipped": stats.ambiguous_skipped,
                "writes": stats.writes,
                "mapping": stats.mapping,
                "table_profiles_rebuilt": table_profiles,
            }

    @app.post("/v1/_test/lookup-citation")
    async def test_lookup_citation(body: CitationLookupRequest) -> dict[str, object]:
        """Invoke ``lookup_citation`` directly against the e2e test DB.

        Accepts the same JSON body as ``CitationLookupRequest``:
        - ``{"citation_path": "4.2"}``
        - ``{"structured": {"kind": "zone_attribute", "zone": "HR-2", "attribute": "max_height"}}``
        - ``{"structured": {"kind": "schedule_row", "schedule": "Table 1A", "row": "HR-2"}}``

        Returns a ``CitationLookupResponse`` dict (``match`` + ``suggestions``).
        Returns 422 when FastAPI/Pydantic rejects the body (missing required
        field, both supplied, unknown attribute vocabulary violation) and 400
        on ``ValueError`` (ambiguous across documents).
        Scoped with no default document resolver so the full test corpus is
        visible — tests can narrow scope via ``document_id`` in the body.
        """
        try:
            with session_scope() as session:
                service = RetrievalService(session)
                response = service.lookup_citation(body)
                return response.model_dump(mode="json")
        except ValueError as exc:
            # Semantic errors from the service: unknown attribute vocabulary,
            # ambiguous-across-documents, etc. All are client input problems,
            # so 422 is the right shape for this test-only endpoint.
            raise HTTPException(status_code=422, detail=str(exc)) from exc

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
    # ABS-431 naming convention: even though this value only lands in
    # synthetic chat tool_result JSON (never a Document row), it must not
    # read as the real bylaw — see scripts/e2e_fixture_names.py.
    bylaw_name: str = "Regional Centre Land Use By-Law (Session Seed E2E)"
    clause_text: str = (
        "The minimum front yard setback shall be 3.0 metres from the property line."
    )
    # Final assistant turn. Overridable so a spec can seed markdown that
    # exercises the renderer — e.g. an attribute table whose cells carry
    # inline citation references (ABS-451).
    assistant_text: str | None = None


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
                        body.assistant_text
                        or "Based on the bylaw evidence, the front yard setback is 3 m."
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


class _ZoneProfileBody(BaseModel):
    zone: str
    include: list[str] | None = None
    # Optional document scope. Production scopes to retrieval-enabled
    # documents (ABS-413); the e2e corpus holds many bylaws, several staging
    # the same zone codes, so a spec passes its own seeded document_id to
    # isolate get_zone_profile to its data (mirrors the document_id scoping
    # on lookup_citation).
    document_id: int | None = None


class _RetrievalFlagBody(BaseModel):
    """ABS-413 e2e driver for the document publish flag."""

    action: Literal["seed", "set", "status"]
    municipality: str = "Test Municipality ABS-413"
    bylaw_name: str = "ABS-413 Retrieval Flag By-law"
    doc_count: int = Field(default=3, ge=1, le=9)
    document_ids: list[int] | None = None
    enabled: bool | None = None
    replace: bool = False


class _SearchEnabledScopeBody(BaseModel):
    query: str
    limit: int = Field(default=10, ge=1, le=50)


class _DisableRetrievalProbeBody(BaseModel):
    """Body for ``POST /v1/_test/disable-retrieval-probe`` (ABS-433).

    Transactionally disables the given documents, probes the production
    enabled scope (evidence search, permitted-use table lookup, address
    profile), then re-enables them — all inside ONE transaction under the
    shared Regional Centre corpus advisory lock. The atomic
    disable→probe→restore shape exists so the probe can never race a
    concurrent seed run (whose convergence pass force-re-enables the unified
    document) and never leaves the shared corpus dark for parallel workers.
    """

    document_ids: list[int]
    query: str
    address: str


def _mount_retrieval_flag_endpoints(app: FastAPI) -> None:
    """ABS-413: drive the real publish surface + production-scope probe.

    ``/v1/_test/retrieval-flag`` seeds N disabled documents (each carrying a
    sentinel fragment), toggles them through the real
    ``layer1.pipeline.publish.set_retrieval_enabled``, and reports status via
    ``list_retrieval_status`` — the exact functions behind the CLI's
    ``enable-retrieval`` / ``disable-retrieval`` / ``list-documents``.

    ``/v1/_test/search-enabled-scope`` runs a search under
    ``retrieval_enabled_resolver`` — the resolver production wires — so the
    Playwright spec can prove opt-in publishing end-to-end: seeded docs are
    invisible until enabled (fail-closed), replace evicts the older sibling,
    and disabling hides them again.
    """
    from datetime import timedelta  # noqa: PLC0415
    import hashlib  # noqa: PLC0415

    from bylaw_retrieval.retrieval import (  # noqa: PLC0415
        RetrievalRequest,
        retrieval_enabled_resolver,
    )
    from layer1.pipeline.publish import (  # noqa: PLC0415
        list_retrieval_status,
        set_retrieval_enabled,
    )

    @app.post("/v1/_test/retrieval-flag")
    async def retrieval_flag(body: _RetrievalFlagBody) -> dict[str, object]:
        with session_scope() as session:
            if body.action == "seed":
                session.query(Document).filter(
                    Document.municipality == body.municipality,
                    Document.bylaw_name == body.bylaw_name,
                ).delete(synchronize_session=False)
                session.flush()
                base_time = utcnow()
                ids: list[int] = []
                for i in range(body.doc_count):
                    file_hash = hashlib.sha256(
                        f"abs413-{body.bylaw_name}-{i}".encode()
                    ).hexdigest()[:64]
                    doc = Document(
                        municipality=body.municipality,
                        bylaw_name=body.bylaw_name,
                        source_path=f"/tmp/abs413-flag-{i}.txt",
                        file_hash=file_hash,
                        mime_type="text/plain",
                        page_count=1,
                        parser_version="e2e-seed",
                        ingestion_timestamp=base_time
                        - timedelta(seconds=(body.doc_count - 1 - i)),
                        # Mirrors real ingest: documents start unpublished.
                        retrieval_enabled=False,
                    )
                    session.add(doc)
                    session.flush()
                    ids.append(doc.id)
                    session.add(
                        SourceFragment(
                            document_id=doc.id,
                            fragment_type=FragmentType.SECTION,
                            citation_label=f"413.{i}",
                            citation_path=f"413.{i}",
                            page_start=1,
                            page_end=1,
                            text=(
                                f"ABS413_FLAG_SENTINEL_V{i} pergola trellis "
                                "height limit for versioned publish testing."
                            ),
                            parse_status=ParseStatus.PARSED,
                            source_block_ids_json=[],
                            metadata_json={},
                        )
                    )
                session.flush()
                return {"document_ids": ids}

            if body.action == "set":
                if body.document_ids is None or body.enabled is None:
                    raise HTTPException(
                        status_code=400,
                        detail="'set' requires document_ids and enabled",
                    )
                try:
                    result = set_retrieval_enabled(
                        session,
                        body.document_ids,
                        body.enabled,
                        replace=body.replace,
                    )
                except ValueError as exc:
                    raise HTTPException(status_code=400, detail=str(exc)) from exc
                return {
                    "changes": [
                        {
                            "document_id": c.document_id,
                            "enabled": c.enabled,
                            "reason": c.reason,
                        }
                        for c in result.changes
                    ],
                    "warnings": result.warnings,
                    "relinked": len(result.relink_results),
                }

            entries = list_retrieval_status(
                session,
                municipality=body.municipality,
                bylaw_name=body.bylaw_name,
            )
            return {
                "documents": [
                    {
                        "id": e.id,
                        "bylaw_name": e.bylaw_name,
                        "retrieval_enabled": e.retrieval_enabled,
                    }
                    for e in entries
                ]
            }

    @app.post("/v1/_test/search-enabled-scope")
    async def search_enabled_scope(body: _SearchEnabledScopeBody) -> dict[str, object]:
        with session_scope() as session:
            service = RetrievalService(
                session, default_document_id_resolver=retrieval_enabled_resolver
            )
            response = service.search(
                RetrievalRequest(query=body.query, top_k=body.limit)
            )
            return {
                "matches": [
                    {
                        "fragment_id": m.fragment_id,
                        "document_id": m.document_id,
                        "text": m.text,
                    }
                    for m in response.matches
                ]
            }

    @app.post("/v1/_test/disable-retrieval-probe")
    async def disable_retrieval_probe(
        body: _DisableRetrievalProbeBody,
    ) -> dict[str, object]:
        """ABS-433: prove disabling the unified RC-LUB document empties scope.

        Runs the real publish surface (``set_retrieval_enabled`` — the same
        function behind the CLI's ``disable-retrieval``), then probes three
        production-scoped surfaces under ``retrieval_enabled_resolver``:

        * the evidence search (fragment scope),
        * the permission-matrix TABLE scope (which enabled documents own a
          matrix — the shared e2e corpus legitimately stages matrices on
          other bylaws, so membership is reported per document id rather
          than as a global emptiness claim),
        * ``get_address_profile`` (linked geo-dataset scope),

        and finally re-enables the documents. Everything happens in one
        transaction while holding the shared Regional Centre corpus advisory
        lock (key mirrors ``scripts/seed_e2e_rclub_unified.py``), so the
        probe can neither race a concurrent seed's convergence re-enable nor
        leave the shared corpus dark for parallel Playwright workers.
        """
        from sqlalchemy import text as sa_text  # noqa: PLC0415

        with session_scope() as session:
            if session.bind.dialect.name == "postgresql":
                # Same key as seed_e2e_rclub_unified.CORPUS_ADVISORY_LOCK_KEY.
                session.execute(
                    sa_text("SELECT pg_advisory_xact_lock(:k)").bindparams(
                        k=2604601273
                    )
                )
            docs = [session.get(Document, doc_id) for doc_id in body.document_ids]
            if any(doc is None for doc in docs):
                raise HTTPException(status_code=400, detail="unknown document id")
            if not all(doc.retrieval_enabled for doc in docs):
                raise HTTPException(
                    status_code=409,
                    detail="probe expects the documents to start enabled",
                )

            def _probe() -> dict[str, object]:
                service = RetrievalService(
                    session, default_document_id_resolver=retrieval_enabled_resolver
                )
                search = service.search(
                    RetrievalRequest(query=body.query, top_k=20)
                )
                # The exact scope lookup_permitted_use resolves against:
                # every permission-matrix table visible under the enabled
                # resolver, reported by owning document id.
                matrix_document_ids = sorted(
                    {
                        table.document_id
                        for table in service._permission_matrix_tables(
                            document_id=None
                        )
                    }
                )
                profile = service.get_address_profile(body.address)
                return {
                    "matches": [
                        {"document_id": m.document_id, "text": m.text}
                        for m in search.matches
                    ],
                    "matrix_document_ids": matrix_document_ids,
                    "zone": profile.zone,
                    "overlay_count": len(profile.overlays),
                    "citation_count": len(profile.citations),
                }

            set_retrieval_enabled(session, body.document_ids, False)
            disabled = _probe()
            set_retrieval_enabled(session, body.document_ids, True)
            restored = _probe()
            return {"disabled": disabled, "restored": restored}


def _mount_zone_profile_endpoint(app: FastAPI) -> None:
    """ABS-272: expose get_zone_profile over HTTP for e2e coverage.

    Calls ``RetrievalService.get_zone_profile`` and returns the compact
    projection plus the ``unknown_zone`` flag so Playwright can assert
    the thick tool composes a full DTO for a known zone and degrades to
    an unknown-zone marker (no 500) for a bogus one.
    """
    from advisor.chat.compact import compact_zone_profile  # noqa: PLC0415
    from bylaw_retrieval.retrieval import RetrievalService  # noqa: PLC0415

    @app.post("/v1/_test/zone-profile")
    async def zone_profile(body: _ZoneProfileBody) -> dict[str, object]:
        with session_scope() as session:
            resolver = (
                (lambda _session, _id=body.document_id: _id)
                if body.document_id is not None
                else None
            )
            service = RetrievalService(session, default_document_id_resolver=resolver)
            profile = service.get_zone_profile(zone=body.zone, include=body.include)
            return {
                "unknown_zone": profile.unknown_zone,
                "citation_count": len(profile.citations),
                "profile": compact_zone_profile(profile),
            }


class _BylawQueryBody(BaseModel):
    intent: str
    address: str | None = None
    zone: str | None = None
    proposed: dict[str, Any] | None = None
    # Scope zone-intent compositions to the spec's own seeded document so the
    # shared e2e corpus (which stages the same zone codes) doesn't bleed in —
    # mirrors the document_id scoping on /v1/_test/zone-profile.
    document_id: int | None = None


def _mount_bylaw_query_endpoint(app: FastAPI) -> None:
    """ABS-274: expose the intent-routed bylaw_query mega-tool over HTTP.

    Calls ``RetrievalService.bylaw_query`` and returns the compact
    projection plus the ``unrecognized_intent`` flag and a conformance
    summary so Playwright can assert the composer routes each intent to the
    right Phase 2/3 composition over the real FastAPI ↔ Postgres boundary,
    and degrades gracefully (no 500) on an unknown intent.
    """
    from advisor.chat.compact import compact_bylaw_query  # noqa: PLC0415
    from bylaw_retrieval.retrieval import RetrievalService  # noqa: PLC0415

    @app.post("/v1/_test/bylaw-query")
    async def bylaw_query(body: _BylawQueryBody) -> dict[str, object]:
        with session_scope() as session:
            resolver = (
                (lambda _session, _id=body.document_id: _id)
                if body.document_id is not None
                else None
            )
            service = RetrievalService(session, default_document_id_resolver=resolver)
            response = service.bylaw_query(
                intent=body.intent,
                address=body.address,
                zone=body.zone,
                proposed=body.proposed,
            )
            return {
                "intent": response.intent,
                "unrecognized_intent": response.unrecognized_intent,
                "suggested_tools": list(response.suggested_tools),
                "conformance_overall": (
                    response.conformance_check.overall
                    if response.conformance_check is not None
                    else None
                ),
                "compact": compact_bylaw_query(response),
            }


class _SpatialCandidateTextBody(BaseModel):
    canonical_attributes: dict[str, Any]
    citation_label: str = "(4.5)"


def _mount_spatial_candidate_text_endpoint(app: FastAPI) -> None:
    """ABS-276: expose _feature_to_candidate rendering over HTTP for e2e coverage.

    Accepts a ``canonical_attributes`` dict and returns the ``text`` field that
    ``_feature_to_candidate`` would produce, exercising the generic attribute
    loop (which replaced the hardcoded height-only rendering) over the real
    FastAPI stack without requiring a seeded ExternalDataset row.
    """
    from unittest.mock import MagicMock  # noqa: PLC0415

    from layer2.models.enums import RetrievalChannel, SourceType  # noqa: PLC0415
    from layer2.models.schemas import CandidateFragment as _CandidateFragment  # noqa: PLC0415
    from layer2.retrieval.spatial import FeatureMatch, _feature_to_candidate  # noqa: PLC0415
    from layer2.retrieval.spatial import ResolvedLocation  # noqa: PLC0415

    @app.post("/v1/_test/spatial-candidate-text")
    async def spatial_candidate_text(body: _SpatialCandidateTextBody) -> dict[str, str]:
        feature = MagicMock()
        feature.canonical_attributes_json = body.canonical_attributes
        feature.id = 9001
        feature.feature_key = "e2e-test-feature"
        feature.geometry_bbox_json = None
        feature.external_dataset_id = 9001

        match = FeatureMatch(feature=feature, overlap_area=500.0, contains_input=True)

        parent = _CandidateFragment(
            source_type=SourceType.DATASET.value,
            retrieval_channel=RetrievalChannel.SPATIAL.value,
            base_score=0.6,
            text="(dataset parent)",
            citation_label=body.citation_label,
        )

        location = ResolvedLocation(
            kind="point",
            geometry={"type": "Point", "coordinates": [-63.58, 44.64]},
            source="e2e",
            reference_text="e2e test point",
        )

        candidate = _feature_to_candidate(match, None, parent, location)
        return {"text": candidate.text}


def _mount_search_evidence_raw_endpoint(app: FastAPI) -> None:
    """ABS-288: expose the raw RetrievalResponse envelope for e2e shape assertions.

    Returns model_dump(mode="json") of the full response so Playwright can
    verify that the request echo is absent and empty fields are omitted.
    """
    from bylaw_retrieval.retrieval import RetrievalRequest, RetrievalService  # noqa: PLC0415

    @app.post("/v1/_test/search-evidence-raw")
    async def search_evidence_raw(body: _SearchEvidenceBody) -> dict[str, object]:
        with session_scope() as session:
            service = RetrievalService(session)
            request = RetrievalRequest(
                query=body.query,
                bylaw_name=body.bylaw_name,
                municipality=body.municipality,
                limit=body.limit,
            )
            response = service.search(request)
            return response.model_dump(mode="json")


def _mount_openai_tool_search_endpoint(app: FastAPI) -> None:
    """ABS-492: drive ``search_bylaw_evidence`` through the OpenAI tool surface.

    ``/v1/_test/search-evidence-raw`` builds a ``RetrievalRequest`` directly, so
    it exercises the request model's defaults, not a tool's. This one goes
    through ``OpenAIToolExecutor`` — the code path an LLM's tool call actually
    takes — with no ``include_*`` keys in the arguments, which is how a model
    calls it in practice: no persona tells it to set the flags.

    That makes the assertion behavioural rather than introspective. The spec
    does not read a flag off a captured request; it reads the ``ancestor_chain``
    off the returned match. After ABS-492 a fragment can rank on scope its
    containers state and it does not, so a match arriving without its chain is
    a rule with its scope stripped off — which is the regression this guards,
    and it is invisible to the ABS-297 guard on the advisor's own handler.
    """
    from typing import Any  # noqa: PLC0415

    from bylaw_retrieval.openai_tools import OpenAIToolExecutor  # noqa: PLC0415

    @app.post("/v1/_test/openai-tool-search")
    async def openai_tool_search(body: _SearchEvidenceBody) -> dict[str, Any]:
        arguments: dict[str, Any] = {"query": body.query, "limit": body.limit}
        if body.bylaw_name:
            arguments["bylaw_name"] = body.bylaw_name
        if body.municipality:
            arguments["municipality"] = body.municipality
        with session_scope() as session:
            return OpenAIToolExecutor(session).execute(
                "search_bylaw_evidence", arguments
            )


def _mount_advisor_search_include_flags_endpoint(app: FastAPI) -> None:
    """ABS-297: WI-7 / WI-3 drift guard.

    Drives ``search_bylaw_evidence_handler`` (the actual advisor production
    handler from ``advisor.chat.tools``) with a capturing stub service and
    returns the ``include_*`` flags that the constructed ``RetrievalRequest``
    carries. The Playwright spec POSTs a default payload (no ``include_*``)
    and asserts all four come back ``True`` — that's the explicit
    ``payload.get(..., True)`` fallback in ``chat/tools.py:774-777`` doing
    its job, overriding the post-WI-7 ``False`` default on
    ``RetrievalRequest``.

    Why a capturing stub instead of the real ``RetrievalService``: the
    drift is at request construction, not at search execution. Cutting the
    DB out of the loop pins the assertion to the handler's flag-passing
    contract and makes the guard independent of seed data (no e2e seed
    currently populates ``linked_datasets``, so a "look at the response
    shape" test would silently pass on an empty fixture).
    """
    from typing import Any  # noqa: PLC0415

    from fastapi import Body  # noqa: PLC0415

    from advisor.chat.tools import build_bylaw_tools  # noqa: PLC0415
    from bylaw_retrieval.retrieval import (  # noqa: PLC0415
        RetrievalRequest,
        RetrievalResponse,
    )

    class _CapturingRetrievalService:
        def __init__(self) -> None:
            self.last_request: RetrievalRequest | None = None

        def search(self, request: RetrievalRequest) -> RetrievalResponse:
            self.last_request = request
            return RetrievalResponse(total_matches=0, matches=[], notes=[])

    @app.post("/v1/_test/advisor-search-include-flags")
    async def advisor_search_include_flags(
        body: dict[str, Any] = Body(...),
    ) -> dict[str, bool | None]:
        if "query" not in body or not isinstance(body.get("query"), str):
            from fastapi import HTTPException  # noqa: PLC0415

            raise HTTPException(status_code=422, detail="missing 'query' (string) in body")
        capturing = _CapturingRetrievalService()
        _, handlers = build_bylaw_tools(capturing)
        await handlers["search_bylaw_evidence"](body)
        req = capturing.last_request
        if req is None:  # pragma: no cover — handler must have called search
            from fastapi import HTTPException  # noqa: PLC0415

            raise HTTPException(status_code=500, detail="handler did not call service.search")
        return {
            "include_context": req.include_context,
            "include_cross_references": req.include_cross_references,
            "include_tables": req.include_tables,
            "include_datasets": req.include_datasets,
        }


def _mount_advisor_search_attribute_tag_filter_endpoint(app: FastAPI) -> None:
    """ABS-479: drive ``attribute_tag_filter`` through the real chat handler.

    The parameter reaches the LLM only if three things line up: it is in the
    tool's JSON Schema, the handler forwards it onto ``RetrievalRequest``, and
    the service turns it into the indexed ``attribute_tags`` clause. Pytest
    covers each link in isolation; this endpoint runs the whole chain inside
    the deployed FastAPI process against the real Postgres, so a missing
    migration-0014 index, a JSONB-operator dialect mismatch (the ``?|``-vs-
    LIKE split that sqlite unit tests mask), or a schema/handler drift trips
    e2e rather than production.

    Returns the tool's own JSON payload on success. On failure it returns
    ``ok: false`` with the error string INSTEAD of a 500 — that mirrors
    ``advisor.llm.tool_loop``, which converts a handler exception into an
    ``is_error`` tool_result the model can correct. The empty-list case is
    exactly that path, so the spec asserts on the error string and a 200.
    """
    import json  # noqa: PLC0415
    from contextlib import contextmanager  # noqa: PLC0415

    from fastapi import Body  # noqa: PLC0415

    from advisor.chat.tools import build_bylaw_tools  # noqa: PLC0415

    @contextmanager
    def _service_factory():
        with session_scope() as session:
            yield RetrievalService(session)

    @app.post("/v1/_test/advisor-search-attribute-tag-filter")
    async def advisor_search_attribute_tag_filter(
        body: dict[str, Any] = Body(...),
    ) -> dict[str, Any]:
        if not isinstance(body.get("query"), str):
            raise HTTPException(status_code=422, detail="missing 'query' (string) in body")
        _, handlers = build_bylaw_tools(_service_factory)
        try:
            raw = await handlers["search_bylaw_evidence"](body)
        except Exception as exc:  # noqa: BLE001 — the tool-error path under test
            return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
        return {"ok": True, "result": json.loads(raw)}


# ---------------------------------------------------------------------------
# ABS-456 / ABS-522: provider resolution, probed in a real process.
#
# ``build_gateway()`` runs exactly once per deployment — at boot, against the
# process environment the service actually inherits. The unit suite reaches it
# with a hand-built ``AdvisorLLMSettings``, which is the right shape for
# pinning the resolution logic but cannot see the two things that decide
# whether a deployment boots:
#
#   * whether ``ADVISOR_LLM_PROVIDER`` is read at all (an alias typo, a
#     settings field renamed out from under its alias, a stray ``.env``
#     shadowing the process env — all invisible to a constructed settings
#     object);
#   * whether a *stale* value in a real environment is rejected rather than
#     coerced. ABS-522 removed the second provider (``claude_code``, the
#     ``claude -p`` CLI). A deployment still carrying that value must fail
#     loudly: silently building the Anthropic gateway would move it from
#     subscription billing to metered billing without a word.
#
# So the probe below spawns one. The child gets an env assembled from scratch —
# never a copy of this server's — runs ``build_gateway()``, and reports what it
# got or how it died. Its cwd is a temp dir so no repo ``.env`` can leak into
# the answer.
# ---------------------------------------------------------------------------

# Env vars the probe will forward. An allowlist, not a passthrough: this
# endpoint hands attacker-controllable strings to a subprocess environment, and
# the e2e server is deliberately unauthenticated. The key is the only one
# ``build_gateway`` gives meaning to now that there is one provider.
_REGISTRY_PROBE_ENV_ALLOWLIST = frozenset({"ANTHROPIC_API_KEY"})

# Fixed program — never assembled from request data. It also reports whether
# the removed CLI backend is importable at all: the removal is only real if the
# module is gone from the installed package, not merely unreferenced.
_REGISTRY_PROBE_SOURCE = """
import importlib.util, json

out = {}
out["cli_backend_importable"] = (
    importlib.util.find_spec("advisor.llm.claude_code_backend") is not None
)
out["cli_translation_importable"] = (
    importlib.util.find_spec("advisor.llm.claude_code_translation") is not None
)
try:
    from advisor.llm.registry import build_gateway
    gateway = build_gateway()
except BaseException as exc:
    out["ok"] = False
    out["error_type"] = type(exc).__name__
    out["error"] = str(exc)
else:
    out["ok"] = True
    out["gateway_name"] = getattr(gateway, "name", None)
    out["gateway_class"] = type(gateway).__name__
print(json.dumps(out))
"""


class _LlmRegistryProbeBody(BaseModel):
    """Body for ``POST /v1/_test/llm-registry-probe`` (ABS-456/522)."""

    # Optional so the spec can probe the "operator sets nothing" case,
    # which is what a container with no ADVISOR_LLM_PROVIDER inherits.
    provider: str | None = Field(default=None, min_length=1, max_length=64)
    env: dict[str, str] = Field(
        default_factory=dict,
        description=(
            "Extra environment for the probe process. Keys must be in "
            "_REGISTRY_PROBE_ENV_ALLOWLIST; anything else is a 400."
        ),
    )


def _mount_llm_registry_probe_endpoint(app: FastAPI) -> None:
    """ABS-456/522: run ``build_gateway()`` in a fresh process and report back."""

    @app.post("/v1/_test/llm-registry-probe")
    async def llm_registry_probe(body: _LlmRegistryProbeBody) -> dict[str, object]:
        import asyncio  # noqa: PLC0415
        import json  # noqa: PLC0415
        import tempfile  # noqa: PLC0415

        unknown = sorted(set(body.env) - _REGISTRY_PROBE_ENV_ALLOWLIST)
        if unknown:
            raise HTTPException(
                status_code=400,
                detail=(
                    f"env keys not allowed for the registry probe: {unknown}. "
                    f"Allowed: {sorted(_REGISTRY_PROBE_ENV_ALLOWLIST)}"
                ),
            )

        # Built from scratch, not os.environ.copy(): this server may well be
        # holding an ANTHROPIC_API_KEY, and inheriting it would make the
        # no-key cases silently untestable. PYTHONPATH carries this process's
        # import path so the child finds ``advisor`` however it was installed.
        env = {
            "PATH": os.environ.get("PATH", ""),
            "PYTHONPATH": os.pathsep.join(p for p in sys.path if p),
            **({} if body.provider is None else {"ADVISOR_LLM_PROVIDER": body.provider}),
            **body.env,
        }

        with tempfile.TemporaryDirectory(prefix="llm-registry-probe-") as cwd:
            process = await asyncio.create_subprocess_exec(
                sys.executable,
                "-c",
                _REGISTRY_PROBE_SOURCE,
                env=env,
                cwd=cwd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            try:
                stdout, stderr = await asyncio.wait_for(
                    process.communicate(), timeout=120
                )
            except TimeoutError:
                process.kill()
                await process.wait()
                raise HTTPException(
                    status_code=504,
                    detail="registry probe did not finish within 120s",
                ) from None

        text = stdout.decode(errors="replace").strip()
        # The probe prints exactly one JSON line, but an import-time warning
        # from a dependency would land on stdout ahead of it.
        payload: dict[str, object] | None = None
        for line in reversed(text.splitlines()):
            try:
                payload = json.loads(line)
            except ValueError:
                continue
            break
        if payload is None:
            raise HTTPException(
                status_code=500,
                detail=(
                    f"registry probe produced no JSON (rc={process.returncode}). "
                    f"stdout={text[-2000:]!r} stderr="
                    f"{stderr.decode(errors='replace')[-2000:]!r}"
                ),
            )
        return {
            "returncode": process.returncode,
            "stderr_tail": stderr.decode(errors="replace")[-2000:],
            **payload,
        }


class _BuyAnswerCheckoutBody(BaseModel):
    user_id: str = Field(default="demo-user-1", min_length=1, max_length=255)
    question_slug: str = Field(min_length=1, max_length=64)
    inputs: dict[str, str] = Field(default_factory=dict)


class _BuyAnswerRunBody(BaseModel):
    purchase_id: int


class _BuyAnswerFreeBalanceBody(BaseModel):
    user_id: str = Field(default="demo-user-1", min_length=1, max_length=255)


class _BuyAnswerGrantFreeBody(BaseModel):
    user_id: str = Field(default="demo-user-1", min_length=1, max_length=255)
    quantity: int = Field(ge=1, le=1000)


class _BuyAnswerRefineBody(BaseModel):
    purchase_id: int
    message: str = Field(min_length=1, max_length=2000)


class _BuyAnswerSlowTurnBody(BaseModel):
    """ABS-338: drive the answer (and optionally a refinement) on a
    connection carrying a deliberately low
    ``idle_in_transaction_session_timeout``, with an LLM turn slower than
    that cap.

    The production 500 was a real-Postgres behaviour: the request
    transaction sat idle for the whole ~84s turn and the server-side cap
    (60s, ABS-100) terminated the connection, so the settling UPDATE raised
    ``IdleInTransactionSessionTimeout``. Waiting 60s in an e2e is absurd, so
    this shrinks the SAME mechanism — 1s cap, 2.5s turn — through the real
    Next-proxy ↔ FastAPI ↔ Postgres chain.
    """

    purchase_id: int
    idle_cap_ms: int = Field(default=1000, ge=100, le=60_000)
    turn_delay_s: float = Field(default=2.5, ge=0.0, le=30.0)
    refine_message: str | None = Field(default=None, max_length=2000)


class _BuyAnswerQuoteBody(BaseModel):
    question: str = Field(min_length=1, max_length=2000)


class _BuyAnswerCheckoutOtherBody(BaseModel):
    user_id: str = Field(default="demo-user-1", min_length=1, max_length=255)
    question: str = Field(min_length=1, max_length=2000)


class _BuyAnswerIntakeBody(BaseModel):
    # ABS-315: consultant-style intake detection. A catalog question slug,
    # the user's free-form conversation so far, and any inputs already
    # confirmed in earlier intake turns.
    question_slug: str = Field(min_length=1, max_length=64)
    conversation: str = Field(default="", max_length=4000)
    inputs: dict[str, str] = Field(default_factory=dict)


def _mount_buy_answer_test_router(app: FastAPI) -> None:
    """ABS-312: drive the priced-question "buy an answer" flow over HTTP.

    Billing stays dormant in the e2e stack (no Stripe), so these
    ``/v1/_test/...`` endpoints exercise the REAL ``advisor.billing
    .answers`` service — start checkout, authorize via the webhook
    handler, run the answer (capture/void), and refine — against the
    test Postgres + the e2e ``MockGateway``, using a ``MockStripeClient``
    in place of live Stripe. This is the same pattern the evaluator /
    address-profile test endpoints use to cover heavy or external-
    dependency code paths through the real stack.
    """
    from advisor.billing import answers as answer_flow  # noqa: PLC0415
    from advisor.billing.answers import (  # noqa: PLC0415
        FreeQuestionsExhaustedError,
        MissingRequiredInputsError,
        NewQuestionError,
        RefinementNotAvailableError,
        UnknownQuestionError,
        WindowExhaustedError,
    )
    from advisor.billing.client import (  # noqa: PLC0415
        CheckoutSessionResult,
        MockStripeClient,
        StripeEvent,
    )
    from advisor.billing.intake import detect_intake  # noqa: PLC0415
    from advisor.billing.questions import question_for  # noqa: PLC0415
    from advisor.billing.quote import (  # noqa: PLC0415
        EmptyQuestionError,
        quote_question,
    )
    from advisor.billing.settings import AdvisorBillingSettings  # noqa: PLC0415
    from advisor.billing.webhooks import handle_event  # noqa: PLC0415
    from advisor.db.models import QuestionPurchase as _QP  # noqa: PLC0415
    from advisor.db.models import User as _User  # noqa: PLC0415

    def _settings() -> AdvisorBillingSettings:
        # Configure every question's Price ID so start_question_checkout
        # resolves a price. Enabled flag is irrelevant here — these
        # endpoints call the service directly, not the gated router.
        return AdvisorBillingSettings(
            ADVISOR_BILLING_ENABLED=True,
            STRIPE_PRICE_QUESTION_PERMITTED_USE="price_test_permitted_use",
            STRIPE_PRICE_QUESTION_DEVELOPMENT_STANDARDS="price_test_dev_standards",
            STRIPE_PRICE_QUESTION_DUE_DILIGENCE="price_test_due_diligence",
            STRIPE_PRICE_QUESTION_LEGAL_NONCONFORMING="price_test_legal_nc",
            STRIPE_PRICE_QUESTION_VARIANCE_JUSTIFICATION="price_test_variance",
        )

    def _mock_client(session_id: str = "cs_test_buy_answer") -> MockStripeClient:
        # Real Stripe issues a unique session id per checkout; mirror that
        # so concurrent e2e workers don't collide on the UNIQUE
        # stripe_checkout_session_id column.
        return MockStripeClient(
            checkout_result=CheckoutSessionResult(
                session_id=session_id, url="https://stripe.test/checkout"
            )
        )

    def _resolve_user(db, user_id: str) -> _User:
        user = (
            db.query(_User).filter(_User.clerk_user_id == user_id).one_or_none()
        )
        if user is None:
            user = _User(clerk_user_id=user_id, email=f"{user_id}@e2e.test")
            db.add(user)
            db.flush()
        return user

    from advisor.billing.report import build_report  # noqa: PLC0415

    def _state(purchase: _QP) -> dict[str, object]:
        return {
            "purchase_id": purchase.id,
            "question_slug": purchase.question_slug,
            "status": purchase.status,
            "answer": purchase.answer_text,
            # ABS-359: the structured report the product surface renders,
            # built by the REAL parser from this purchase's captured answer —
            # so an e2e can assert the deliverable is monologue-free end-to-end
            # rather than stubbing already-clean data at the network boundary.
            "report": build_report(purchase),
            "failure_reason": purchase.failure_reason,
            "refinement_count": purchase.refinement_count,
            "refinements_remaining": answer_flow.refinements_remaining(purchase),
            "window_expires_at": (
                purchase.window_expires_at.isoformat()
                if purchase.window_expires_at is not None
                else None
            ),
        }

    @app.post("/v1/_test/buy-answer/intake")
    async def buy_answer_intake(
        body: _BuyAnswerIntakeBody,
    ) -> dict[str, object]:
        # ABS-315: consultant-style intake detection against the real
        # advisor.billing.intake service + e2e MockGateway. No purchase
        # row, no Stripe — detecting/asking for inputs never charges. The
        # mock resolves a deterministic extraction from MOCK_INPUT[...]
        # sentinels in the conversation.
        try:
            question = question_for(body.question_slug)
        except KeyError as exc:
            raise HTTPException(
                status_code=400,
                detail=f"unknown question {body.question_slug!r}",
            ) from exc
        result = await detect_intake(
            app.state.gateway,
            question,
            conversation=body.conversation,
            provided_inputs=body.inputs,
        )
        return {
            "question_slug": question.slug,
            "complete": result.complete,
            "inputs": result.inputs,
            "missing_required": result.missing_required,
            "missing_optional": result.missing_optional,
            "prompt": result.prompt,
        }

    @app.post("/v1/_test/buy-answer/checkout")
    async def buy_answer_checkout(
        body: _BuyAnswerCheckoutBody,
    ) -> dict[str, object]:
        settings = _settings()
        session_id = f"cs_test_{uuid.uuid4().hex[:16]}"
        with session_scope() as db:
            user = _resolve_user(db, body.user_id)
            try:
                purchase, _url = answer_flow.start_question_checkout(
                    db,
                    user,
                    question_slug=body.question_slug,
                    inputs=body.inputs,
                    client=_mock_client(session_id),
                    settings=settings,
                )
            except UnknownQuestionError as exc:
                raise HTTPException(status_code=400, detail=str(exc)) from exc
            except MissingRequiredInputsError as exc:
                raise HTTPException(
                    status_code=400,
                    detail={"code": "missing_required_inputs", "missing": exc.missing},
                ) from exc
            purchase_id = purchase.id
            # Simulate the checkout.session.completed webhook authorizing
            # the manual-capture PaymentIntent — exercises the real
            # webhook router path that flips the purchase to "authorized".
            event = StripeEvent(
                id=f"evt_test_{purchase_id}",
                type="checkout.session.completed",
                data={
                    "id": session_id,
                    "payment_intent": f"pi_test_{purchase_id}",
                    "metadata": {
                        "advisor_user_id": str(user.id),
                        "question_purchase_id": str(purchase_id),
                        "question_slug": body.question_slug,
                    },
                },
            )
            handle_event(db, event, settings)
            db.flush()
            purchase = db.get(_QP, purchase_id)
            return _state(purchase)

    @app.post("/v1/_test/buy-answer/checkout-free")
    async def buy_answer_checkout_free(
        body: _BuyAnswerCheckoutBody,
    ) -> dict[str, object]:
        # ABS-322: payments-off / free-trial checkout. Consumes one
        # free-question credit and lands the purchase straight in
        # "authorized" — NO Stripe, no webhook. Returns ok=False with
        # code=free_questions_exhausted when the trial is used up so the
        # spec can assert the exhaustion state.
        with session_scope() as db:
            user = _resolve_user(db, body.user_id)
            try:
                purchase = answer_flow.start_question_free(
                    db,
                    user,
                    question_slug=body.question_slug,
                    inputs=body.inputs,
                )
            except UnknownQuestionError as exc:
                raise HTTPException(status_code=400, detail=str(exc)) from exc
            except MissingRequiredInputsError as exc:
                raise HTTPException(
                    status_code=400,
                    detail={
                        "code": "missing_required_inputs",
                        "missing": exc.missing,
                    },
                ) from exc
            except FreeQuestionsExhaustedError:
                return {
                    "ok": False,
                    "code": "free_questions_exhausted",
                    "free_questions_remaining": user.free_questions_remaining,
                }
            purchase_id = purchase.id
            remaining = user.free_questions_remaining
            db.flush()
            purchase = db.get(_QP, purchase_id)
            return {
                "ok": True,
                "free_questions_remaining": remaining,
                **_state(purchase),
            }

    @app.post("/v1/_test/buy-answer/free-balance")
    async def buy_answer_free_balance(
        body: _BuyAnswerFreeBalanceBody,
    ) -> dict[str, object]:
        # ABS-322: read the user's current free-question entitlement so a
        # spec can assert grant / consume / refund deltas.
        with session_scope() as db:
            user = _resolve_user(db, body.user_id)
            return {
                "user_id": body.user_id,
                "free_questions_remaining": user.free_questions_remaining,
            }

    @app.post("/v1/_test/buy-answer/grant-free-questions")
    async def buy_answer_grant_free(
        body: _BuyAnswerGrantFreeBody,
    ) -> dict[str, object]:
        # ABS-322: exercise the admin-grant-more-free-questions service
        # path (advisor.db.cases.grant_free_questions) end-to-end.
        from advisor.db.cases import grant_free_questions  # noqa: PLC0415

        with session_scope() as db:
            user = _resolve_user(db, body.user_id)
            remaining = grant_free_questions(
                db,
                user=user,
                quantity=body.quantity,
                reason="e2e-grant",
            )
            return {
                "user_id": body.user_id,
                "granted": body.quantity,
                "free_questions_remaining": remaining,
            }

    @app.post("/v1/_test/buy-answer/quote")
    async def buy_answer_quote(
        body: _BuyAnswerQuoteBody,
    ) -> dict[str, object]:
        # ABS-316: produce a FREE off-menu price quote against the real
        # advisor.billing.quote service + e2e MockGateway. No purchase
        # row, no Stripe — quoting never charges.
        try:
            quote = await quote_question(app.state.gateway, body.question)
        except EmptyQuestionError as exc:
            raise HTTPException(
                status_code=400,
                detail={"code": "empty_question", "message": str(exc)},
            ) from exc
        return {
            "question": quote.question_text,
            "difficulty": quote.difficulty,
            "difficulty_display_name": quote.difficulty_display_name,
            "price_cents": quote.price_cents,
            "currency": quote.currency,
            "rationale": quote.rationale,
            "cumulative_token_budget": quote.cumulative_token_budget,
            "band_low_cents": quote.band_low_cents,
            "band_high_cents": quote.band_high_cents,
        }

    @app.post("/v1/_test/buy-answer/checkout-other")
    async def buy_answer_checkout_other(
        body: _BuyAnswerCheckoutOtherBody,
    ) -> dict[str, object]:
        # ABS-316: re-quote server-side, then authorize an ad-hoc
        # manual-capture checkout for the quoted amount. Mirrors the
        # catalog checkout endpoint: simulate the
        # checkout.session.completed webhook so the purchase lands in
        # "authorized" ready for /answer.
        settings = _settings()
        session_id = f"cs_test_{uuid.uuid4().hex[:16]}"
        with session_scope() as db:
            user = _resolve_user(db, body.user_id)
            try:
                quote = await quote_question(app.state.gateway, body.question)
            except EmptyQuestionError as exc:
                raise HTTPException(
                    status_code=400,
                    detail={"code": "empty_question", "message": str(exc)},
                ) from exc
            purchase, _url = answer_flow.start_other_checkout(
                db,
                user,
                quote=quote,
                client=_mock_client(session_id),
                settings=settings,
            )
            purchase_id = purchase.id
            event = StripeEvent(
                id=f"evt_test_{purchase_id}",
                type="checkout.session.completed",
                data={
                    "id": session_id,
                    "payment_intent": f"pi_test_{purchase_id}",
                    "metadata": {
                        "advisor_user_id": str(user.id),
                        "question_purchase_id": str(purchase_id),
                        "question_slug": "other",
                    },
                },
            )
            handle_event(db, event, settings)
            db.flush()
            purchase = db.get(_QP, purchase_id)
            return {
                "price_cents": quote.price_cents,
                "difficulty": quote.difficulty,
                "rationale": quote.rationale,
                **_state(purchase),
            }

    @app.post("/v1/_test/buy-answer/answer")
    async def buy_answer_run(body: _BuyAnswerRunBody) -> dict[str, object]:
        with session_scope() as db:
            purchase = db.get(_QP, body.purchase_id)
            if purchase is None:
                raise HTTPException(status_code=404, detail="purchase not found")
            purchase = await answer_flow.run_answer(
                db,
                purchase,
                gateway=app.state.gateway,
                persona=app.state.persona_text,
                retrieval_factory=app.state.retrieval_factory,
                client=_mock_client(),
            )
            return _state(purchase)

    @app.post("/v1/_test/buy-answer/answer-slow-turn")
    async def buy_answer_run_slow(
        body: _BuyAnswerSlowTurnBody,
    ) -> dict[str, object]:
        """ABS-338: run a SLOW answer turn under a low idle-in-txn cap.

        Reproduces the production 500 in miniature against the real e2e
        Postgres. Before the fix the request transaction stayed open across
        the turn, so the cap terminated the connection and the settling
        ``db.flush()`` raised ``IdleInTransactionSessionTimeout`` → HTTP 500.
        After it, ``run_answer`` / ``run_refinement`` hold no transaction
        while the turn runs, so there is nothing for the cap to kill.

        The aggressive per-session cap rides a DEDICATED ``NullPool`` engine
        so it can never leak onto a pooled connection another request reuses.
        """
        import asyncio  # noqa: PLC0415

        from sqlalchemy import create_engine, text  # noqa: PLC0415
        from sqlalchemy.orm import Session  # noqa: PLC0415
        from sqlalchemy.pool import NullPool  # noqa: PLC0415

        from layer1.config import get_settings as _layer1_settings  # noqa: PLC0415

        class _SlowGateway:
            """Delegates to the e2e MockGateway, but makes the turn's first
            LLM call outlast the idle cap pinned on the request connection."""

            name = "mock"

            def __init__(self, inner: Any, delay_s: float) -> None:
                self._inner = inner
                self._delay_s = delay_s
                self._slept = False

            async def _maybe_sleep(self) -> None:
                if not self._slept:
                    self._slept = True
                    await asyncio.sleep(self._delay_s)

            async def complete(self, request):  # noqa: ANN001
                await self._maybe_sleep()
                return await self._inner.complete(request)

            async def stream(self, request):  # noqa: ANN001
                await self._maybe_sleep()
                async for event in self._inner.stream(request):
                    yield event

        def _pin_idle_cap(db: Session) -> None:
            if db.bind is not None and db.bind.dialect.name == "postgresql":
                db.execute(
                    text(
                        "SET SESSION idle_in_transaction_session_timeout = "
                        f"{int(body.idle_cap_ms)}"
                    )
                )

        engine = create_engine(
            _layer1_settings().database_url, poolclass=NullPool
        )
        try:
            db = Session(bind=engine, expire_on_commit=False, future=True)
            try:
                _pin_idle_cap(db)
                purchase = db.get(_QP, body.purchase_id)
                if purchase is None:
                    raise HTTPException(
                        status_code=404, detail="purchase not found"
                    )
                purchase = await answer_flow.run_answer(
                    db,
                    purchase,
                    gateway=_SlowGateway(app.state.gateway, body.turn_delay_s),
                    persona=app.state.persona_text,
                    retrieval_factory=app.state.retrieval_factory,
                    client=_mock_client(),
                )
                state = _state(purchase)
                db.commit()
            finally:
                db.close()

            # A non-captured answer has nothing to refine — hand the state
            # back so the spec fails on the real assertion (status) rather
            # than on an opaque RefinementNotAvailableError 500.
            if not body.refine_message or state.get("status") != "captured":
                return state

            # Sibling proof for run_refinement — a fresh low-cap connection
            # and another slow turn, since the same flaw lived in both.
            db = Session(bind=engine, expire_on_commit=False, future=True)
            try:
                _pin_idle_cap(db)
                purchase = db.get(_QP, body.purchase_id)
                answer = await answer_flow.run_refinement(
                    db,
                    purchase,
                    message=body.refine_message,
                    gateway=_SlowGateway(app.state.gateway, body.turn_delay_s),
                    persona=app.state.persona_text,
                    retrieval_factory=app.state.retrieval_factory,
                )
                state = {"refined_answer": answer, **_state(purchase)}
                db.commit()
                return state
            finally:
                db.close()
        finally:
            engine.dispose()

    @app.post("/v1/_test/buy-answer/refine")
    async def buy_answer_refine(body: _BuyAnswerRefineBody) -> dict[str, object]:
        with session_scope() as db:
            purchase = db.get(_QP, body.purchase_id)
            if purchase is None:
                raise HTTPException(status_code=404, detail="purchase not found")
            try:
                answer = await answer_flow.run_refinement(
                    db,
                    purchase,
                    message=body.message,
                    gateway=app.state.gateway,
                    persona=app.state.persona_text,
                    retrieval_factory=app.state.retrieval_factory,
                )
            except NewQuestionError as exc:
                return {
                    "ok": False,
                    "code": "new_question",
                    "suggested_slug": exc.suggested_slug,
                    **_state(purchase),
                }
            except WindowExhaustedError as exc:
                return {
                    "ok": False,
                    "code": "window_exhausted",
                    "reason": exc.reason,
                    **_state(purchase),
                }
            except RefinementNotAvailableError as exc:
                return {
                    "ok": False,
                    "code": "refinement_unavailable",
                    "message": str(exc),
                    **_state(purchase),
                }
            return {"ok": True, "answer": answer, **_state(purchase)}

    @app.post("/v1/_test/buy-answer/get")
    async def buy_answer_get(body: _BuyAnswerRunBody) -> dict[str, object]:
        with session_scope() as db:
            purchase = db.get(_QP, body.purchase_id)
            if purchase is None:
                raise HTTPException(status_code=404, detail="purchase not found")
            return _state(purchase)


app = build_e2e_app()
_mount_seed_session_endpoint(app)
_mount_retrieval_flag_endpoints(app)
_mount_search_evidence_endpoint(app)
_mount_search_evidence_raw_endpoint(app)
_mount_zone_profile_endpoint(app)
_mount_bylaw_query_endpoint(app)
_mount_spatial_candidate_text_endpoint(app)
_mount_openai_tool_search_endpoint(app)
_mount_advisor_search_include_flags_endpoint(app)
_mount_advisor_search_attribute_tag_filter_endpoint(app)
_mount_llm_registry_probe_endpoint(app)
_mount_buy_answer_test_router(app)


if __name__ == "__main__":  # pragma: no cover
    import uvicorn

    uvicorn.run(
        "advisor.api.e2e_server:app",
        host=os.environ.get("ADVISOR_HOST", "127.0.0.1"),
        port=int(os.environ.get("ADVISOR_PORT", "8001")),
        reload=False,
    )
