from datetime import datetime, timezone
from pathlib import Path

from layer1.datasets.linker import (
    find_orphan_datasets,
    link_dataset_to_bylaw,
    relink_orphan_datasets,
)
from layer1.db.base import Document, ExternalDataset, SourceFragment
from layer1.db.init_db import create_all
from layer1.db.session import session_scope
from layer1.models.enums import FragmentType, ParseStatus
from layer1.pipeline.ingest_dataset import ingest_geo_dataset


MINI_FIXTURE_CONFIG = """
name: mini_height_precincts
publisher: Test
format: geojson
source_path: tests/fixtures/geo/mini_height_precincts.geojson
crs: EPSG:4326
links_to:
  document_match:
    municipality: Halifax Regional Municipality
    bylaw_name: Regional Centre Land Use By-law
  fragment_citation: Schedule 15
attributes:
  feature_key: GLOBALID
  canonical:
    max_height_m: { from: HEIGHT, type: float }
    display_label: { synthesize: "{HEIGHT}m precinct" }
  ignore: [OBJECTID, SACC]
"""


def _setup_db(tmp_path: Path) -> str:
    db_url = f"sqlite:///{tmp_path / 'layer1.db'}"
    create_all(db_url)
    return db_url


def _seed_synthetic_bylaw(
    session, *, municipality="Halifax Regional Municipality",
    bylaw_name="Regional Centre Land Use By-law",
    schedule_label="Schedule 15",
) -> tuple[Document, SourceFragment]:
    document = Document(
        municipality=municipality,
        bylaw_name=bylaw_name,
        source_path="/synthetic.pdf",
        file_hash="deadbeef" * 8,
        mime_type="application/pdf",
        ingestion_timestamp=datetime.now(timezone.utc),
    )
    session.add(document)
    session.flush()
    fragment = SourceFragment(
        document_id=document.id,
        fragment_type=FragmentType.SCHEDULE,
        citation_label=schedule_label,
        citation_path=f"schedules.{schedule_label.replace(' ', '_').lower()}",
        page_start=500,
        page_end=502,
        text=f"{schedule_label}: Maximum Building Height Precincts.",
        parse_status=ParseStatus.PARSED,
        source_block_ids_json=[],
        metadata_json={},
    )
    session.add(fragment)
    session.flush()
    return document, fragment


def _write_config(tmp_path: Path) -> Path:
    cfg_path = tmp_path / "mini.yaml"
    cfg_path.write_text(MINI_FIXTURE_CONFIG)
    return cfg_path


def test_dataset_links_when_bylaw_already_present(tmp_path: Path):
    db_url = _setup_db(tmp_path)
    cfg_path = _write_config(tmp_path)

    with session_scope(db_url) as session:
        document, fragment = _seed_synthetic_bylaw(session)
        document_id, fragment_id = document.id, fragment.id

    with session_scope(db_url) as session:
        result = ingest_geo_dataset(session, cfg_path)
        assert result.link_result.status == "linked"
        assert result.link_result.fragment_id == fragment_id
        assert result.dataset.linked_document_id == document_id
        assert result.dataset.linked_fragment_id == fragment_id


def test_dataset_first_then_bylaw_relinks(tmp_path: Path):
    db_url = _setup_db(tmp_path)
    cfg_path = _write_config(tmp_path)

    # Ingest dataset before any bylaw exists — must persist as orphan, not crash.
    with session_scope(db_url) as session:
        result = ingest_geo_dataset(session, cfg_path)
        assert result.link_result.status == "no_document"
        assert result.dataset.linked_fragment_id is None
        dataset_id = result.dataset.id

    # Verify orphan visible to audit:
    with session_scope(db_url) as session:
        orphans = find_orphan_datasets(session)
        assert len(orphans) == 1
        assert orphans[0].id == dataset_id

    # Now ingest the bylaw and run relink:
    with session_scope(db_url) as session:
        _, fragment = _seed_synthetic_bylaw(session)
        fragment_id = fragment.id
        results = relink_orphan_datasets(session)
        assert len(results) == 1
        assert results[0].status == "linked"
        assert results[0].fragment_id == fragment_id

    # Orphan list now empty:
    with session_scope(db_url) as session:
        assert find_orphan_datasets(session) == []
        dataset = session.get(ExternalDataset, dataset_id)
        assert dataset.linked_fragment_id == fragment_id
        # Link history records both attempts:
        history = dataset.metadata_json.get("link_history") or []
        assert len(history) == 2
        assert history[0]["status"] == "no_document"
        assert history[1]["status"] == "linked"


def test_no_matching_fragment_records_orphan(tmp_path: Path):
    db_url = _setup_db(tmp_path)
    cfg_path = _write_config(tmp_path)

    # Bylaw exists but with a different schedule label:
    with session_scope(db_url) as session:
        _seed_synthetic_bylaw(session, schedule_label="Schedule 14")

    with session_scope(db_url) as session:
        result = ingest_geo_dataset(session, cfg_path)
        assert result.link_result.status == "no_fragment"
        assert result.link_result.document_id is not None
        assert result.dataset.linked_fragment_id is None


def test_ambiguous_fragment_does_not_link_silently(tmp_path: Path):
    db_url = _setup_db(tmp_path)
    cfg_path = _write_config(tmp_path)

    # Same citation_label twice on the same document is technically guarded
    # by uq_fragment_citation_path (different paths allowed), so seed two
    # fragments sharing label but with different citation_paths.
    with session_scope(db_url) as session:
        document, _ = _seed_synthetic_bylaw(session)
        duplicate = SourceFragment(
            document_id=document.id,
            fragment_type=FragmentType.SCHEDULE,
            citation_label="Schedule 15",
            citation_path="schedules.schedule_15.alternate",
            page_start=600,
            page_end=601,
            text="Duplicate Schedule 15 reference.",
            parse_status=ParseStatus.PARSED,
            source_block_ids_json=[],
            metadata_json={},
        )
        session.add(duplicate)
        session.flush()

    with session_scope(db_url) as session:
        result = ingest_geo_dataset(session, cfg_path)
        assert result.link_result.status == "ambiguous_fragment"
        assert result.dataset.linked_fragment_id is None
        assert "2 fragments" in result.link_result.detail


def test_multiple_documents_picks_most_recent(tmp_path: Path):
    db_url = _setup_db(tmp_path)
    cfg_path = _write_config(tmp_path)

    with session_scope(db_url) as session:
        # Older document with the schedule:
        old_doc, old_frag = _seed_synthetic_bylaw(session)
        # Force a clearly-older timestamp:
        old_doc.ingestion_timestamp = datetime(2020, 1, 1, tzinfo=timezone.utc)
        session.flush()

    # Newer reingest of the same bylaw:
    with session_scope(db_url) as session:
        new_doc, new_frag = _seed_synthetic_bylaw(session)
        new_frag_id = new_frag.id

    with session_scope(db_url) as session:
        result = ingest_geo_dataset(session, cfg_path)
        assert result.link_result.status == "linked"
        assert result.link_result.fragment_id == new_frag_id
        assert "most recent" in result.link_result.detail


def test_direct_link_function_handles_unknown_dataset_id(tmp_path: Path):
    db_url = _setup_db(tmp_path)
    with session_scope(db_url) as session:
        try:
            link_dataset_to_bylaw(session, 999_999)
        except ValueError as exc:
            assert "999999" in str(exc) or "not found" in str(exc)
        else:
            raise AssertionError("expected ValueError for unknown dataset_id")
