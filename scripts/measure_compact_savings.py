"""Measure the byte/JSON-size savings of compact tool_result payloads.

Runs an address-based ``search_bylaw_evidence`` call against an in-memory
seeded database with a real spatial dataset linked to a fragment — the
same shape that drove the production cost incident on 2026-05-11 (a
59-character user prompt that consumed 611k input tokens because the
spatial retrieval blob got replayed across every tool-loop turn).

Reports:
  - full JSON size  (status quo: model_dump(mode="json"))
  - compact size    (advisor.chat.compact projection)
  - delta + ratio

Run with:
  python scripts/measure_compact_savings.py
"""
from __future__ import annotations

import json
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from advisor.chat.compact import compact_search_response
from bylaw_retrieval.retrieval import (
    LocationSlot,
    RetrievalRequest,
    RetrievalService,
)
from layer1.db.base import Document, SourceFragment
from layer1.db.init_db import create_all as create_layer1
from layer1.db.session import session_scope
from layer1.models.enums import FragmentType, ParseStatus
from layer1.pipeline.ingest_dataset import ingest_geo_dataset
from layer2.db.init_db import create_all as create_layer2


HEIGHT_CONFIG = """
name: mini_height_precincts_h
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
  feature_key: GlobalID
  canonical:
    max_height_m: { from: MAXBLDHGT, type: float, optional: true }
    max_height_storeys: { from: MAXBLDSTRY, type: int, optional: true }
  ignore: [OBJECTID, SACC]
"""


def _seed(tmp: Path) -> str:
    db_url = f"sqlite:///{tmp / 'measure.db'}"
    create_layer1(db_url)
    create_layer2(db_url)

    with session_scope(db_url) as session:
        document = Document(
            municipality="Halifax Regional Municipality",
            bylaw_name="Regional Centre Land Use By-law",
            source_path="/synthetic.pdf",
            file_hash="h" * 64,
            mime_type="application/pdf",
            ingestion_timestamp=datetime.now(timezone.utc),
            page_count=600,
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
            reading_order_start=1,
            reading_order_end=1,
            text="Schedule 15: Maximum Building Height Precincts.",
            parse_status=ParseStatus.PARSED,
            source_block_ids_json=[],
            metadata_json={},
        )
        session.add(fragment)

    cfg_path = tmp / "height.yaml"
    cfg_path.write_text(HEIGHT_CONFIG)
    with session_scope(db_url) as session:
        ingest_geo_dataset(session, cfg_path)
    return db_url


def _amplify_to_production_shape(response):
    """The fixture only has one fragment linked to one dataset. Real
    address searches in production return ~5-8 matches, each fragment
    typically linked to 3-6 datasets (zone, height precinct, FAR,
    heritage, bonus zoning, shadow impact), each dataset returning a
    handful of feature_matches. Synthesise that shape on top of the
    real fixture so the size delta reflects the actual cost driver
    rather than a one-match toy.
    """
    from bylaw_retrieval.retrieval.schemas import (
        AncestorFragment,
        CrossReferenceSummary,
        DatasetFeatureMatch,
        LinkedDataset,
        RetrievalMatch,
        TableCellSummary,
        TableSummary,
    )

    base = response.matches[0]
    base_dataset = base.linked_datasets[0] if base.linked_datasets else None
    inflated: list[RetrievalMatch] = []
    dataset_names = [
        "halifax_zones",
        "halifax_height_precincts",
        "halifax_far_precincts",
        "halifax_heritage_districts",
        "halifax_bonus_zoning",
        "halifax_shadow_impact",
    ]
    for i in range(8):
        linked = []
        for j, name in enumerate(dataset_names):
            feature_matches = []
            for k in range(2):
                feature_matches.append(
                    DatasetFeatureMatch(
                        feature_id=1000 + j * 10 + k,
                        feature_key=f"GlobalID-{j}-{k}-{'x' * 24}",
                        canonical_attributes={
                            "max_height_m": 25.0 + j,
                            "max_height_storeys": 6 + j,
                            "zone_code": f"ER-{j}",
                            "far": 2.5 + j * 0.1,
                        },
                        contains_input=(k == 0),
                        overlap_metric=0.42 + 0.01 * j,
                    )
                )
            linked.append(
                LinkedDataset(
                    dataset_id=200 + j,
                    name=name,
                    publisher="Halifax Regional Municipality",
                    feature_count=137 + j * 7,
                    crs="EPSG:4326",
                    summary_text=(
                        f"Dataset {name} contains land-use precincts as polygons "
                        f"in EPSG:4326 with attributes for max height, storey count, "
                        f"and FAR. Sourced from HRM open data, ingested by the "
                        f"layer1 dataset pipeline. " * 2
                    ),
                    source_image_id=500 + j,
                    feature_matches=feature_matches,
                    location_resolver="google_maps",
                    location_confidence=0.95,
                )
            )

        ancestors = [
            AncestorFragment(
                id=10 + a,
                fragment_type="part" if a == 0 else "division",
                citation_label=f"Part {a + 1}",
                citation_path=f"part_{a + 1}",
                page_start=10 + a * 5,
                page_end=12 + a * 5,
                text=(
                    f"This Part {a + 1} sets out the regulations governing "
                    f"land use precincts within the Regional Centre, including "
                    f"interpretive rules and structural provisions that frame "
                    f"the rest of the by-law. " * 2
                ),
            )
            for a in range(3)
        ]
        xrefs = [
            CrossReferenceSummary(
                id=100 + x,
                raw_reference_text=f"see Schedule {x + 1}",
                target_citation_guess=f"schedules.schedule_{x + 1}",
                resolution_status="resolved",
                confidence=0.9,
                target_fragment_id=900 + x,
                target_citation_path=f"schedules.schedule_{x + 1}",
            )
            for x in range(4)
        ]
        tables = [
            TableSummary(
                id=300 + i,
                caption=f"Permitted uses in zone ER-{i}",
                page_start=100 + i,
                page_end=101 + i,
                parse_status="parsed",
                parent_fragment_id=base.fragment_id,
                cells=[
                    TableCellSummary(
                        row_index=r,
                        col_index=c,
                        text=f"r{r}c{c}",
                        row_header_path=f"row.path.{r}",
                        col_header_path=f"col.path.{c}",
                    )
                    for r in range(4)
                    for c in range(4)
                ],
            )
        ]

        inflated.append(
            RetrievalMatch(
                fragment_id=base.fragment_id + i,
                document_id=base.document_id,
                municipality=base.municipality,
                bylaw_name=base.bylaw_name,
                fragment_type=base.fragment_type,
                citation_label=f"Schedule {15 + i}",
                citation_path=f"schedules.schedule_{15 + i}",
                page_start=500 + i,
                page_end=502 + i,
                parse_status=base.parse_status,
                confidence=base.confidence,
                text=(
                    f"Schedule {15 + i}: Maximum Building Height Precincts. "
                    f"This schedule prescribes the maximum permitted building "
                    f"height for each precinct shown on the corresponding map. "
                ),
                score=110.0 - i * 5,
                retrieval_channels=["text", "spatial"],
                ancestor_chain=ancestors,
                cross_references=xrefs,
                related_tables=tables,
                linked_datasets=linked,
                metadata_json={
                    "ingest_run_id": 42,
                    "source_block_ids": list(range(20)),
                    "page_anchors": [500, 501, 502],
                },
            )
        )
    response.matches = inflated
    response.total_matches = len(inflated)
    return response


def _report(label, response):
    full_json = json.dumps(response.model_dump(mode="json"))
    compact_json = json.dumps(compact_search_response(response))

    full_size = len(full_json)
    compact_size = len(compact_json)
    delta = full_size - compact_size
    ratio = compact_size / full_size if full_size else 1.0

    print(label)
    print(f"  matches returned: {len(response.matches)}")
    print(f"  full     JSON bytes: {full_size:>7}")
    print(f"  compact  JSON bytes: {compact_size:>7}")
    print(f"  delta            :  {delta:>7}  ({(1 - ratio) * 100:.1f}% smaller)")
    print()


def main() -> None:
    with tempfile.TemporaryDirectory() as tmp_str:
        tmp = Path(tmp_str)
        db_url = _seed(tmp)
        with session_scope(db_url) as session:
            service = RetrievalService(session)
            response = service.search(
                RetrievalRequest(
                    query="maximum building height",
                    location=LocationSlot(
                        geometry={"type": "Point", "coordinates": [-63.59, 44.65]}
                    ),
                    limit=8,
                )
            )

    _report("Real fixture (1 match, 1 linked dataset):", response)

    inflated = _amplify_to_production_shape(response)
    _report(
        "Production-shape (8 matches x 6 datasets, with ancestors/xrefs/tables):",
        inflated,
    )

    # Pagination: simulate a caller that requested limit=25 (max) and
    # returned 20 matches; with the default cap of 10 the compact
    # response ships the top 10 + a truncation note.
    import os as _os

    _os.environ["ADVISOR_COMPACT_MAX_MATCHES"] = "10"
    inflated.matches = inflated.matches * 3  # 24 matches
    inflated.total_matches = len(inflated.matches)
    _report(
        "High-limit caller (24 matches in -> 10 shown via pagination):",
        inflated,
    )

    compact_json = json.dumps(compact_search_response(inflated))
    print("Compact preview of production-shape payload (first 600 chars):")
    print(compact_json[:600] + ("..." if len(compact_json) > 600 else ""))


if __name__ == "__main__":
    main()
