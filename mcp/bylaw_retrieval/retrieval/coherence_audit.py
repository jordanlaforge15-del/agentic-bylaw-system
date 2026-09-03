"""Corpus-coherence audit (ABS-356).

When a linked geo dataset falls out of retrieval scope — a bad link, a
document-version eviction, a seed collision (the ABS-349/ABS-350 saga) —
``get_address_profile`` degrades silently: the affected overlay field comes
back ``None`` and a paid answer hedges instead of citing a schedule. Every
prior incident was discovered downstream, in an unrelated e2e failure or a
customer-visible answer.

This module asserts a cheap invariant instead: for every overlay role a
dataset config *declares* (``links_to`` in a ``layer1.datasets`` YAML file),
there must be a linked ``ExternalDataset`` of that role actually visible
through :func:`bylaw_retrieval.retrieval.service.scoped_linked_datasets` for
its bylaw. A missing role is classified into exactly one of three modes,
using the vocabulary already established by ``layer1.datasets.linker`` and
the ABS-350 postmortem:

* ``unlinked``  — no dataset with the declared name was ever ingested.
* ``orphaned``  — the dataset exists but the linker never resolved it to a
  fragment (``ExternalDataset.linked_fragment_id IS NULL`` — see
  ``layer1.datasets.linker.find_orphan_datasets``).
* ``evicted``   — the dataset is linked to a real fragment, but that
  fragment's document is outside the active retrieval scope (not
  retrieval-enabled — e.g. an amendment was ingested and published while the
  dataset stayed pinned to the now-disabled version; the ABS-350 regression
  shape).

Usage (programmatic)::

    from bylaw_retrieval.retrieval.coherence_audit import audit_corpus_coherence
    report = audit_corpus_coherence(session, default_document_id_resolver=retrieval_enabled_resolver)

Usage (CLI)::

    .venv/bin/python scripts/corpus_coherence_audit.py
"""
from __future__ import annotations

from pathlib import Path
from typing import Sequence

from sqlalchemy import select
from sqlalchemy.orm import Session

from layer1 import datasets as layer1_datasets
from layer1.datasets.config import DatasetConfig, load_dataset_config
from layer1.db.base import (
    Document,
    ExternalDataset,
    ExternalDatasetFeature,
    SourceFragment,
)
from layer1.naming import normalized_document_identity

from bylaw_retrieval.retrieval.schemas import (
    CorpusCoherenceReport,
    E2eContaminationMarker,
    E2eContaminationReport,
    EnabledDocumentRef,
    EnabledNameCollision,
    EnabledNameCollisionReport,
    GoverningBylawCoverageReport,
    MissingOverlayRole,
    OverlayDeclaration,
    UnheldGoverningBylaw,
)
from bylaw_retrieval.retrieval.service import (
    DocumentIdResolver,
    governing_document_for_bylaw_name,
    overlay_role_for_name,
    scoped_linked_datasets,
)

# The real dataset config directory, resolved through the installed
# ``layer1.datasets`` package rather than by walking up from this file
# (ABS-420). Both resolutions agree in a repo checkout — the package IS
# src/layer1/datasets — but only this one survives a wheel install, where the
# code lives in site-packages and there is no src/ tree above it. The old
# parents[3] walk resolved to /opt/venv/lib/python3.11/src/layer1/datasets in
# the advisor image: a path that never existed, whose .glob() returned nothing,
# so /v1/monitoring/corpus-coherence answered {"status":"ok","checked_roles":0}
# in production no matter how broken the corpus was. The tripwire built for the
# ABS-349/350 degradation had never fired in the one place it guards.
#
# The YAML files reach site-packages via [tool.setuptools.package-data];
# tests/test_package_data.py builds a real wheel and asserts they are in it.
DEFAULT_DATASET_CONFIG_DIR = Path(layer1_datasets.__file__).resolve().parent


def _overlay_configs(config_dir: Path) -> list[DatasetConfig]:
    """Load every dataset config in ``config_dir`` that declares a bylaw link.

    Role-bearing configs (``civic_address``, ``property_parcels``,
    ``road_centerlines``) are excluded — they don't bind to a bylaw fragment
    and aren't "overlay roles" in the sense this audit checks (mirrors
    ``layer1.datasets.linker.find_orphan_datasets``, which excludes the same
    class of dataset from its orphan scan).
    """
    configs = []
    for path in sorted(config_dir.glob("*.yaml")):
        config = load_dataset_config(path)
        if config.role is not None or config.links_to is None:
            continue
        configs.append(config)
    return configs


def load_overlay_declarations(config_dir: Path | str = DEFAULT_DATASET_CONFIG_DIR) -> list[OverlayDeclaration]:
    """Read every overlay-role dataset config under ``config_dir`` off disk.

    Raises ``FileNotFoundError`` when ``config_dir`` does not exist. An audit
    that cannot read its declarations knows nothing, and the shape of "knows
    nothing" here used to be an empty ``glob()`` — indistinguishable, at every
    call site, from "every declared role is visible" (ABS-420). Callers that
    legitimately audit a subset pass ``overlay_declarations`` directly; nobody
    passes a directory they expect to be absent.
    """
    directory = Path(config_dir)
    if not directory.is_dir():
        raise FileNotFoundError(
            f"dataset config directory {directory} does not exist — the "
            "corpus-coherence audit cannot load its overlay declarations"
        )
    configs = _overlay_configs(directory)
    return [
        OverlayDeclaration(
            dataset_name=config.name,
            municipality=config.links_to.document_match.municipality,
            bylaw_name=config.links_to.document_match.bylaw_name,
            fragment_citation=config.links_to.fragment_citation,
        )
        for config in configs
    ]


def audit_corpus_coherence(
    session: Session,
    *,
    overlay_declarations: Sequence[OverlayDeclaration] | None = None,
    dataset_config_dir: Path | str | None = None,
    default_document_id_resolver: DocumentIdResolver | None = None,
) -> CorpusCoherenceReport:
    """Assert every declared overlay role is visible in the active retrieval scope.

    ``overlay_declarations`` lets a caller (tests, the e2e test-only
    endpoint) supply the "what should be linked" side directly rather than
    reading YAML off disk. When omitted, every config under
    ``dataset_config_dir`` (default: the real ``src/layer1/datasets/``) is
    scanned — the production/CLI/ops-surface default.

    ``default_document_id_resolver`` should mirror whatever a deployment
    wires into its ``RetrievalService`` (e.g. ``retrieval_enabled_resolver``)
    so the audit sees the same "active" scope real requests do. Passing
    ``None`` audits against the unscoped corpus — every ingested document —
    which is only useful for diagnosing raw linker state, not what a real
    request would see.
    """
    declarations = (
        list(overlay_declarations)
        if overlay_declarations is not None
        else load_overlay_declarations(dataset_config_dir or DEFAULT_DATASET_CONFIG_DIR)
    )

    missing: list[MissingOverlayRole] = []
    bylaws_checked: set[tuple[str, str]] = set()

    for declaration in declarations:
        bylaws_checked.add((declaration.municipality, declaration.bylaw_name))
        role = overlay_role_for_name(declaration.dataset_name)

        scoped = scoped_linked_datasets(
            session,
            default_document_id_resolver=default_document_id_resolver,
            municipality=declaration.municipality,
            bylaw_name=declaration.bylaw_name,
        )
        visible_roles = {overlay_role_for_name(dataset.name) for dataset in scoped}
        if role in visible_roles:
            continue

        missing.append(
            _classify_missing_role(session, declaration=declaration, role=role)
        )

    return CorpusCoherenceReport(
        coherent=not missing,
        checked_roles=len(declarations),
        bylaws_checked=len(bylaws_checked),
        missing=missing,
    )


def _classify_missing_role(
    session: Session, *, declaration: OverlayDeclaration, role: str
) -> MissingOverlayRole:
    dataset = session.execute(
        select(ExternalDataset).where(ExternalDataset.name == declaration.dataset_name)
    ).scalars().first()

    if dataset is None:
        reason = "unlinked"
        detail = (
            f"no dataset named {declaration.dataset_name!r} has ever been ingested; "
            f"the config declares role {role!r} for {declaration.bylaw_name!r} but "
            "nothing backs it"
        )
    elif dataset.linked_fragment_id is None:
        link_status = (dataset.metadata_json or {}).get("link_status")
        reason = "orphaned"
        detail = (
            f"dataset {declaration.dataset_name!r} exists but is not linked to any "
            f"fragment (link_status={link_status!r}); it declares role {role!r} for "
            f"{declaration.bylaw_name!r}"
        )
    else:
        fragment = session.get(SourceFragment, dataset.linked_fragment_id)
        document_id = fragment.document_id if fragment is not None else None
        reason = "evicted"
        detail = (
            f"dataset {declaration.dataset_name!r} is linked to fragment "
            f"{dataset.linked_fragment_id} in document {document_id}, but that "
            f"document is not in the active retrieval scope for {declaration.bylaw_name!r} "
            "(evicted by a newer ingest of the same bylaw)"
        )

    return MissingOverlayRole(
        role=role,
        dataset_name=declaration.dataset_name,
        municipality=declaration.municipality,
        bylaw_name=declaration.bylaw_name,
        fragment_citation=declaration.fragment_citation,
        reason=reason,
        detail=detail,
    )


# ---------------------------------------------------------------------------
# ABS-472 — governing-by-law coverage
# ---------------------------------------------------------------------------
#
# The audit above asks "is every declared overlay role visible?". This one
# asks the question a municipality-wide layer forces: of the ground that layer
# maps, how much is governed by a by-law we actually hold? HRM's zoning layer
# carries 11,069 features across 22 by-law areas and the corpus holds two of
# them, so a spatial query can land on real, correctly-mapped ground whose
# standards live in a document we never ingested.
#
# Deliberately NOT part of ``coherent``: an incomplete answer here is the
# expected steady state, not a regression. It is exposure to size and act on,
# and the per-request refusal already lives on ``AddressProfile``.


def audit_governing_bylaw_coverage(
    session: Session,
    *,
    default_document_id_resolver: DocumentIdResolver | None = None,
) -> GoverningBylawCoverageReport:
    """Count features whose governing by-law is outside the retrieval corpus.

    Walks every in-scope linked dataset that declares
    ``links_to.governing_bylaw_from`` (see ``layer1.datasets.config``), groups
    its features by the by-law they attribute themselves to, and resolves each
    against the corpus with the same rule ``get_address_profile`` uses.
    """
    scoped_ids = _scoped_document_ids(session, default_document_id_resolver)
    datasets_checked = 0
    features_checked = 0
    covered = 0
    unheld: list[UnheldGoverningBylaw] = []

    for dataset in scoped_linked_datasets(
        session, default_document_id_resolver=default_document_id_resolver
    ):
        config = ((dataset.metadata_json or {}).get("links_to") or {}).get(
            "governing_bylaw_from"
        )
        if not isinstance(config, dict) or not config.get("name_attribute"):
            continue
        datasets_checked += 1
        municipality = None
        if dataset.linked_document_id is not None:
            linked = session.get(Document, dataset.linked_document_id)
            municipality = linked.municipality if linked is not None else None

        counts = _governing_bylaw_counts(session, dataset, config)
        for (name, code), count in sorted(counts.items(), key=lambda kv: -kv[1]):
            features_checked += count
            document = governing_document_for_bylaw_name(
                session,
                name,
                municipality=municipality,
                scoped_document_ids=scoped_ids,
            )
            if document is not None:
                covered += count
                continue
            unheld.append(
                UnheldGoverningBylaw(
                    dataset_name=dataset.name,
                    governing_bylaw=name,
                    governing_bylaw_code=code,
                    feature_count=count,
                    detail=(
                        f"{count} feature(s) in {dataset.name!r} are governed by "
                        f"{name!r}, which is not in the retrieval corpus; they "
                        "resolve to a zone with no citation and a 'not_held' "
                        "governing-bylaw status"
                    ),
                )
            )

    unheld.sort(key=lambda row: -row.feature_count)
    return GoverningBylawCoverageReport(
        complete=not unheld,
        datasets_checked=datasets_checked,
        features_checked=features_checked,
        covered_features=covered,
        unheld_features=features_checked - covered,
        unheld=unheld,
    )


def _scoped_document_ids(
    session: Session, resolver: DocumentIdResolver | None
) -> list[int] | None:
    if resolver is None:
        return None
    result = resolver(session)
    if result is None:
        return None
    return [result] if isinstance(result, int) else list(result)


def _governing_bylaw_counts(
    session: Session, dataset: ExternalDataset, config: dict
) -> dict[tuple[str, str | None], int]:
    """Feature counts per (governing by-law name, code) for one dataset.

    Counted in Python over the canonical attributes rather than in SQL: the
    JSON-extraction syntax differs between Postgres and the SQLite the unit
    tests run on, and this is an ops audit over a five-figure row count, not a
    request path.
    """
    name_attribute = config["name_attribute"]
    code_attribute = config.get("code_attribute")
    counts: dict[tuple[str, str | None], int] = {}
    rows = session.execute(
        select(ExternalDatasetFeature.canonical_attributes_json).where(
            ExternalDatasetFeature.external_dataset_id == dataset.id
        )
    ).scalars()
    for canonical in rows:
        canonical = canonical or {}
        name = canonical.get(name_attribute)
        if not isinstance(name, str) or not name.strip():
            continue
        code = canonical.get(code_attribute) if code_attribute else None
        key = (name, code if isinstance(code, str) and code else None)
        counts[key] = counts.get(key, 0) + 1
    return counts


# ---------------------------------------------------------------------------
# ABS-432 — e2e contamination sweep
# ---------------------------------------------------------------------------
#
# The three fingerprints every scripts/seed_e2e_*.py fixture stamps on the
# rows it creates. Kept here — next to the coherence audit that shares the
# same ops surfaces (CLI, /v1/monitoring/corpus-coherence) — so the
# dev-up.sh preflight, the CLI, the monitoring endpoint, and the ABS-420
# prod sweep all agree on one definition of "e2e marker".

E2E_PARSER_VERSION_MARKER = "e2e-seed"
E2E_FILE_HASH_PREFIX = "e2e-"
E2E_DATASET_NAME_PREFIX = "e2e_"


def audit_e2e_contamination(session: Session) -> E2eContaminationReport:
    """Sweep the connected database for e2e-suite fixture markers (ABS-432).

    Flags every row matching any of:

    * ``document.parser_version = 'e2e-seed'``
    * ``document.file_hash LIKE 'e2e-%'``
    * ``external_dataset.name LIKE 'e2e_%'`` (literal underscore — escaped,
      so a hypothetical ``e2eX...`` name does not false-positive)

    Pure read-only SELECTs; safe to point at prod. The caller decides what a
    non-empty result means: in a dev/prod database it is contamination that
    should refuse a boot or turn monitoring red; in the e2e stack's own
    database these rows are the suite's legitimate fixtures.
    """
    marker_counts = {
        "document_parser_version": 0,
        "document_file_hash": 0,
        "external_dataset_name": 0,
    }
    markers: list[E2eContaminationMarker] = []

    documents = (
        session.execute(
            select(Document)
            .where(
                (Document.parser_version == E2E_PARSER_VERSION_MARKER)
                | Document.file_hash.startswith(E2E_FILE_HASH_PREFIX, autoescape=True)
            )
            .order_by(Document.id)
        )
        .scalars()
        .all()
    )
    for document in documents:
        kinds: list[str] = []
        if document.parser_version == E2E_PARSER_VERSION_MARKER:
            kinds.append("document_parser_version")
            marker_counts["document_parser_version"] += 1
        if document.file_hash.startswith(E2E_FILE_HASH_PREFIX):
            kinds.append("document_file_hash")
            marker_counts["document_file_hash"] += 1
        markers.append(
            E2eContaminationMarker(
                table="document",
                row_id=document.id,
                marker_kinds=kinds,
                detail=(
                    f"document {document.id}: {document.bylaw_name!r} "
                    f"({document.municipality}), file_hash={document.file_hash!r}, "
                    f"parser_version={document.parser_version!r}"
                ),
            )
        )

    datasets = (
        session.execute(
            select(ExternalDataset)
            .where(ExternalDataset.name.startswith(E2E_DATASET_NAME_PREFIX, autoescape=True))
            .order_by(ExternalDataset.id)
        )
        .scalars()
        .all()
    )
    for dataset in datasets:
        marker_counts["external_dataset_name"] += 1
        markers.append(
            E2eContaminationMarker(
                table="external_dataset",
                row_id=dataset.id,
                marker_kinds=["external_dataset_name"],
                detail=f"external_dataset {dataset.id}: name={dataset.name!r}",
            )
        )

    return E2eContaminationReport(
        contaminated=bool(markers),
        marker_counts=marker_counts,
        markers=markers,
    )


# ---------------------------------------------------------------------------
# ABS-434 — enabled-name-collision audit
# ---------------------------------------------------------------------------


def audit_enabled_name_collisions(session: Session) -> EnabledNameCollisionReport:
    """At most one ENABLED document per normalized bylaw identity (ABS-434).

    The doc-15/38 double-enable happened because two enabled documents
    shared a bylaw name modulo casing ("By-law" vs "By-Law"): the
    migration-0024 backfill, the ``enable-retrieval`` sibling detection and
    ``--replace``, and the ABS-355 relink all match ``(municipality,
    bylaw_name)`` with literal equality, so name drift silently fragments
    the enabled corpus into pieces no pass can reconcile.

    Groups every retrieval-enabled document by its case/hyphen/whitespace-
    normalized ``(municipality, bylaw_name)`` (``layer1.naming`` — the same
    normalizer the ABS-431 fixture-name guard uses); any group with more
    than one enabled document is a violation reported with the ids and the
    stored spellings. Read-only; safe to point at prod.
    """
    documents = (
        session.execute(
            select(Document)
            .where(Document.retrieval_enabled.is_(True))
            .order_by(Document.id)
        )
        .scalars()
        .all()
    )

    groups: dict[tuple[str, str], list[Document]] = {}
    for document in documents:
        key = normalized_document_identity(document.municipality, document.bylaw_name)
        groups.setdefault(key, []).append(document)

    collisions: list[EnabledNameCollision] = []
    for (norm_municipality, norm_bylaw_name), members in sorted(groups.items()):
        if len(members) <= 1:
            continue
        spellings = ", ".join(
            f"{doc.id}: {doc.municipality!r} / {doc.bylaw_name!r}" for doc in members
        )
        collisions.append(
            EnabledNameCollision(
                normalized_municipality=norm_municipality,
                normalized_bylaw_name=norm_bylaw_name,
                document_ids=[doc.id for doc in members],
                documents=[
                    EnabledDocumentRef(
                        id=doc.id,
                        municipality=doc.municipality,
                        bylaw_name=doc.bylaw_name,
                    )
                    for doc in members
                ],
                detail=(
                    f"{len(members)} enabled documents share the normalized bylaw "
                    f"identity ({norm_municipality!r}, {norm_bylaw_name!r}): "
                    f"{spellings}. Exact-match passes (backfill, --replace, "
                    "relink) cannot reconcile them — disable the stray or fix "
                    "the name drift."
                ),
            )
        )

    return EnabledNameCollisionReport(
        collision_free=not collisions,
        enabled_documents=len(documents),
        identities_checked=len(groups),
        collisions=collisions,
    )
