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
  fragment's document fell outside the active retrieval scope (superseded by
  a newer ingest of the same bylaw — the exact ABS-350 regression).

Usage (programmatic)::

    from bylaw_retrieval.retrieval.coherence_audit import audit_corpus_coherence
    report = audit_corpus_coherence(session, default_document_id_resolver=latest_per_bylaw_resolver)

Usage (CLI)::

    .venv/bin/python scripts/corpus_coherence_audit.py
"""
from __future__ import annotations

from pathlib import Path
from typing import Sequence

from sqlalchemy import select
from sqlalchemy.orm import Session

from layer1.datasets.config import DatasetConfig, load_dataset_config
from layer1.db.base import ExternalDataset, SourceFragment

from bylaw_retrieval.retrieval.schemas import (
    CorpusCoherenceReport,
    MissingOverlayRole,
    OverlayDeclaration,
)
from bylaw_retrieval.retrieval.service import (
    DocumentIdResolver,
    overlay_role_for_name,
    scoped_linked_datasets,
)

# src/layer1/datasets/ — the real dataset config directory, resolved
# relative to this file so the audit works regardless of cwd.
DEFAULT_DATASET_CONFIG_DIR = Path(__file__).resolve().parents[3] / "src" / "layer1" / "datasets"


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
    """Read every overlay-role dataset config under ``config_dir`` off disk."""
    configs = _overlay_configs(Path(config_dir))
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
    wires into its ``RetrievalService`` (e.g. ``latest_per_bylaw_resolver``)
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
