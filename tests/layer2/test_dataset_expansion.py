from datetime import datetime, timezone
from pathlib import Path

import pytest

from layer1.db.base import Document, ExternalDataset, ExternalDatasetFeature, SourceFragment
from layer1.db.init_db import create_all as create_layer1
from layer1.db.session import session_scope
from layer1.models.enums import FragmentType, ParseStatus
from layer1.pipeline.ingest_dataset import ingest_geo_dataset
from layer2.db.init_db import create_all as create_layer2
from layer2.models.enums import RetrievalChannel, SourceType
from layer2.models.schemas import CandidateFragment
from layer2.retrieval.datasets import expand_datasets


MINI_FIXTURE_CONFIG = """
name: mini_height_precincts_layer2
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


@pytest.fixture()
def linked_dataset(tmp_path: Path):
    db_url = f"sqlite:///{tmp_path / 'layer.db'}"
    create_layer1(db_url)
    create_layer2(db_url)

    with session_scope(db_url) as session:
        document = Document(
            municipality="Halifax Regional Municipality",
            bylaw_name="Regional Centre Land Use By-law",
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
            citation_label="Schedule 15",
            citation_path="schedules.schedule_15",
            page_start=500,
            page_end=502,
            text="Schedule 15: Maximum Building Height Precincts.",
            parse_status=ParseStatus.PARSED,
            source_block_ids_json=[],
            metadata_json={},
        )
        session.add(fragment)
        session.flush()
        document_id = document.id
        fragment_id = fragment.id

    cfg_path = tmp_path / "mini.yaml"
    cfg_path.write_text(MINI_FIXTURE_CONFIG)
    with session_scope(db_url) as session:
        result = ingest_geo_dataset(session, cfg_path)
        dataset_id = result.dataset.id
        assert result.link_result.status == "linked"

    return {
        "db_url": db_url,
        "document_id": document_id,
        "fragment_id": fragment_id,
        "dataset_id": dataset_id,
    }


def test_expand_emits_dataset_candidate_when_fragment_is_linked(linked_dataset):
    with session_scope(linked_dataset["db_url"]) as session:
        candidates = [
            CandidateFragment(
                source_fragment_id=linked_dataset["fragment_id"],
                source_type=SourceType.FRAGMENT.value,
                retrieval_channel=RetrievalChannel.FULL_TEXT.value,
                base_score=0.8,
                text="Schedule 15: Maximum Building Height Precincts.",
            )
        ]
        expanded = expand_datasets(session, candidates)

    dataset_candidates = [c for c in expanded if c.source_type == SourceType.DATASET.value]
    assert len(dataset_candidates) == 1
    dc = dataset_candidates[0]
    assert dc.external_dataset_id == linked_dataset["dataset_id"]
    assert dc.retrieval_channel == RetrievalChannel.DATASET.value
    assert dc.citation_label == "Schedule 15"
    # The summary text must mention what makes it useful for the LLM:
    assert "Schedule 15" in dc.text
    assert "mini_height_precincts_layer2" in dc.text
    assert "3 feature" in dc.text
    assert "25 m" in dc.text and "50 m" in dc.text
    assert dc.reason["expansion"] == "linked_dataset"
    assert dc.reason["dataset_name"] == "mini_height_precincts_layer2"


def test_expand_does_nothing_when_no_fragment_is_linked(tmp_path: Path):
    db_url = f"sqlite:///{tmp_path / 'layer.db'}"
    create_layer1(db_url)
    create_layer2(db_url)

    with session_scope(db_url) as session:
        candidates = [
            CandidateFragment(
                source_fragment_id=999,
                source_type=SourceType.FRAGMENT.value,
                retrieval_channel=RetrievalChannel.FULL_TEXT.value,
                base_score=0.8,
                text="Some unrelated text.",
            )
        ]
        expanded = expand_datasets(session, candidates)
    assert expanded == candidates  # passthrough, no datasets in this DB


def test_expand_does_nothing_for_pure_dataset_candidates(linked_dataset):
    """When the candidate stream already contains a DATASET candidate for
    the relevant dataset, expansion must not double-emit."""
    with session_scope(linked_dataset["db_url"]) as session:
        candidates = [
            CandidateFragment(
                source_fragment_id=linked_dataset["fragment_id"],
                source_type=SourceType.FRAGMENT.value,
                retrieval_channel=RetrievalChannel.FULL_TEXT.value,
                base_score=0.8,
                text="Schedule 15 prose.",
            ),
            CandidateFragment(
                external_dataset_id=linked_dataset["dataset_id"],
                source_type=SourceType.DATASET.value,
                retrieval_channel=RetrievalChannel.DATASET.value,
                base_score=0.55,
                text="(pre-seeded dataset candidate)",
            ),
        ]
        expanded = expand_datasets(session, candidates)
    dataset_candidates = [c for c in expanded if c.source_type == SourceType.DATASET.value]
    assert len(dataset_candidates) == 1  # the pre-seeded one, not duplicated


def test_orphan_dataset_is_not_emitted(linked_dataset):
    """Orphan datasets (linked_fragment_id is null) must not appear as
    candidates — that data hasn't been validated against any bylaw fragment."""
    with session_scope(linked_dataset["db_url"]) as session:
        # Manually orphan the dataset:
        dataset = session.get(ExternalDataset, linked_dataset["dataset_id"])
        dataset.linked_fragment_id = None
        session.flush()

    with session_scope(linked_dataset["db_url"]) as session:
        candidates = [
            CandidateFragment(
                source_fragment_id=linked_dataset["fragment_id"],
                source_type=SourceType.FRAGMENT.value,
                retrieval_channel=RetrievalChannel.FULL_TEXT.value,
                base_score=0.8,
                text="Schedule 15 prose.",
            )
        ]
        expanded = expand_datasets(session, candidates)
    assert all(c.source_type != SourceType.DATASET.value for c in expanded)


def test_dataset_candidate_carries_feature_count_in_reason(linked_dataset):
    with session_scope(linked_dataset["db_url"]) as session:
        candidates = [
            CandidateFragment(
                source_fragment_id=linked_dataset["fragment_id"],
                source_type=SourceType.FRAGMENT.value,
                retrieval_channel=RetrievalChannel.FULL_TEXT.value,
                base_score=0.8,
                text="anything",
            )
        ]
        expanded = expand_datasets(session, candidates)
    dataset_candidate = next(c for c in expanded if c.source_type == SourceType.DATASET.value)
    assert dataset_candidate.reason["feature_count"] == 3
