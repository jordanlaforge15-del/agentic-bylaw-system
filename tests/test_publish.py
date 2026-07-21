"""ABS-413 — ``layer1.pipeline.publish``: the operator publish surface.

``set_retrieval_enabled`` / ``list_retrieval_status`` back the layer1 CLI's
``enable-retrieval`` / ``disable-retrieval`` / ``list-documents`` commands and
the e2e test endpoint. Key semantics under test: all-or-nothing identity
checks, sibling replace/warn behavior, publish-driven geo-dataset relinking,
the zero-enabled-corpus warning, and dry-run leaving state untouched.
"""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest

from layer1.db.base import Document, ExternalDataset, SourceFragment
from layer1.db.init_db import create_all
from layer1.db.session import session_scope
from layer1.models.enums import FragmentType, ParseStatus
from layer1.pipeline.publish import list_retrieval_status, set_retrieval_enabled

BYLAW = "Regional Centre Land Use By-law"


def _add_version(
    session,
    *,
    bylaw_name: str = BYLAW,
    schedule_label: str = "Schedule 15",
    enabled: bool = False,
    ingested: datetime | None = None,
    hash_char: str = "a",
) -> tuple[int, int]:
    doc = Document(
        municipality="HRM",
        bylaw_name=bylaw_name,
        source_path=f"/{hash_char}.pdf",
        file_hash=hash_char * 64,
        mime_type="application/pdf",
        ingestion_timestamp=ingested or datetime(2026, 1, 1, tzinfo=timezone.utc),
        retrieval_enabled=enabled,
    )
    session.add(doc)
    session.flush()
    fragment = SourceFragment(
        document_id=doc.id,
        fragment_type=FragmentType.SCHEDULE,
        citation_label=schedule_label,
        citation_path=f"schedules.{schedule_label.replace(' ', '_').lower()}",
        page_start=1,
        page_end=1,
        text=f"{schedule_label}: Maximum Building Height Precincts.",
        parse_status=ParseStatus.PARSED,
        source_block_ids_json=[],
        metadata_json={},
    )
    session.add(fragment)
    session.flush()
    return doc.id, fragment.id


def _add_dataset(session, *, document_id: int, fragment_id: int, citation: str = "Schedule 15") -> int:
    dataset = ExternalDataset(
        name="test_height_precincts",
        format="geojson",
        content_hash="h-height",
        crs="EPSG:4326",
        feature_count=1,
        linked_document_id=document_id,
        linked_fragment_id=fragment_id,
        linked_fragment_citation=citation,
        schema_mapping_json={},
        parse_status=ParseStatus.PARSED,
        metadata_json={"link_status": "linked"},
    )
    session.add(dataset)
    session.flush()
    return dataset.id


@pytest.fixture()
def db_url(tmp_path: Path) -> str:
    url = f"sqlite:///{tmp_path / 'publish.db'}"
    create_all(url)
    return url


def test_enable_and_disable_roundtrip(db_url: str):
    with session_scope(db_url) as session:
        doc_id, _ = _add_version(session)
        result = set_retrieval_enabled(session, [doc_id], True)
        assert [c.document_id for c in result.changes] == [doc_id]
        assert session.get(Document, doc_id).retrieval_enabled is True

        result = set_retrieval_enabled(session, [doc_id], False)
        assert [c.document_id for c in result.changes] == [doc_id]
        assert session.get(Document, doc_id).retrieval_enabled is False


def test_unknown_id_raises_and_changes_nothing(db_url: str):
    with session_scope(db_url) as session:
        doc_id, _ = _add_version(session)
        with pytest.raises(ValueError, match="999999"):
            set_retrieval_enabled(session, [doc_id, 999_999], True)
        assert session.get(Document, doc_id).retrieval_enabled is False


def test_enabling_with_enabled_sibling_warns_without_replace(db_url: str):
    with session_scope(db_url) as session:
        v1_id, _ = _add_version(session, enabled=True, hash_char="a")
        v2_id, _ = _add_version(session, hash_char="b")
        result = set_retrieval_enabled(session, [v2_id], True)
        assert any("other enabled version" in w for w in result.warnings)
        # Both stay enabled — representable, but warned about.
        assert session.get(Document, v1_id).retrieval_enabled is True
        assert session.get(Document, v2_id).retrieval_enabled is True


def test_replace_disables_sibling_in_same_transaction(db_url: str):
    with session_scope(db_url) as session:
        v1_id, _ = _add_version(session, enabled=True, hash_char="a")
        v2_id, _ = _add_version(session, hash_char="b")
        result = set_retrieval_enabled(session, [v2_id], True, replace=True)
        assert session.get(Document, v1_id).retrieval_enabled is False
        assert session.get(Document, v2_id).retrieval_enabled is True
        reasons = {c.document_id: c.reason for c in result.changes}
        assert reasons == {v1_id: "replaced-sibling", v2_id: "requested"}
        assert not any("other enabled version" in w for w in result.warnings)


def test_replace_ignores_other_bylaws(db_url: str):
    with session_scope(db_url) as session:
        other_id, _ = _add_version(
            session, bylaw_name="Mainland Land Use By-law", enabled=True, hash_char="c"
        )
        v2_id, _ = _add_version(session, hash_char="b")
        set_retrieval_enabled(session, [v2_id], True, replace=True)
        assert session.get(Document, other_id).retrieval_enabled is True


def test_enable_runs_relink_onto_published_version(db_url: str):
    # The ABS-355 amendment workflow, now publish-driven: the dataset pinned
    # to v1 follows the newly published v2.
    with session_scope(db_url) as session:
        v1_id, v1_frag = _add_version(session, enabled=True, hash_char="a")
        dataset_id = _add_dataset(session, document_id=v1_id, fragment_id=v1_frag)
        v2_id, v2_frag = _add_version(session, hash_char="b")

        result = set_retrieval_enabled(session, [v2_id], True, replace=True)

        assert len(result.relink_results) == 1
        assert result.relink_results[0].status == "linked"
        dataset = session.get(ExternalDataset, dataset_id)
        assert dataset.linked_document_id == v2_id
        assert dataset.linked_fragment_id == v2_frag


def test_disabling_last_document_warns_empty_corpus(db_url: str):
    with session_scope(db_url) as session:
        doc_id, _ = _add_version(session, enabled=True)
        result = set_retrieval_enabled(session, [doc_id], False)
        assert any("empty results" in w for w in result.warnings)


def test_disabling_with_survivors_does_not_warn_empty(db_url: str):
    with session_scope(db_url) as session:
        keep_id, _ = _add_version(
            session, bylaw_name="Mainland Land Use By-law", enabled=True, hash_char="c"
        )
        drop_id, _ = _add_version(session, enabled=True, hash_char="a")
        result = set_retrieval_enabled(session, [drop_id], False)
        assert not any("empty results" in w for w in result.warnings)


def test_already_in_state_warns_without_change(db_url: str):
    with session_scope(db_url) as session:
        doc_id, _ = _add_version(session, enabled=True)
        result = set_retrieval_enabled(session, [doc_id], True)
        assert result.changes == []
        assert any("already enabled" in w for w in result.warnings)
        result = set_retrieval_enabled(session, [doc_id], False)
        result = set_retrieval_enabled(session, [doc_id], False)
        assert any("already disabled" in w for w in result.warnings)


def test_dry_run_reports_but_mutates_nothing(db_url: str):
    with session_scope(db_url) as session:
        v1_id, v1_frag = _add_version(session, enabled=True, hash_char="a")
        dataset_id = _add_dataset(session, document_id=v1_id, fragment_id=v1_frag)
        v2_id, _ = _add_version(session, hash_char="b")

        result = set_retrieval_enabled(session, [v2_id], True, replace=True, dry_run=True)

        reasons = {c.document_id: c.reason for c in result.changes}
        assert reasons == {v1_id: "replaced-sibling", v2_id: "requested"}
        assert result.relink_results == []
        assert session.get(Document, v1_id).retrieval_enabled is True
        assert session.get(Document, v2_id).retrieval_enabled is False
        assert session.get(ExternalDataset, dataset_id).linked_document_id == v1_id

        # Dry-run disable of the only enabled doc still predicts the
        # empty-corpus warning.
        result = set_retrieval_enabled(session, [v1_id], False, dry_run=True)
        assert any("empty results" in w for w in result.warnings)
        assert session.get(Document, v1_id).retrieval_enabled is True


def test_list_retrieval_status_filters(db_url: str):
    with session_scope(db_url) as session:
        v1_id, _ = _add_version(session, enabled=True, hash_char="a")
        v2_id, _ = _add_version(session, hash_char="b")
        other_id, _ = _add_version(
            session, bylaw_name="Mainland Land Use By-law", hash_char="c"
        )

        entries = list_retrieval_status(session)
        assert {e.id for e in entries} == {v1_id, v2_id, other_id}
        assert {e.id: e.retrieval_enabled for e in entries}[v1_id] is True

        enabled_only = list_retrieval_status(session, enabled_only=True)
        assert [e.id for e in enabled_only] == [v1_id]

        by_name = list_retrieval_status(session, bylaw_name="Mainland")
        assert [e.id for e in by_name] == [other_id]
