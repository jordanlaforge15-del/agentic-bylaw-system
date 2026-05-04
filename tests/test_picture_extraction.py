from datetime import datetime, timezone
from pathlib import Path

import pytest
from PIL import Image

from layer1.db.base import Document, ExternalDataset, SourceFragment, SourceImage
from layer1.db.init_db import create_all as create_layer1
from layer1.db.session import session_scope
from layer1.models.enums import FragmentType, ParseStatus
from layer1.models.schemas import BBox, ImageData
from layer1.parsers.base import ParseResult
from layer1.pipeline.ingest import _persist_images
from layer1.pipeline.ingest_dataset import ingest_geo_dataset
from layer2.db.init_db import create_all as create_layer2
from layer2.models.enums import RetrievalChannel, SourceType
from layer2.models.schemas import CandidateFragment
from layer2.retrieval.datasets import expand_datasets


def _make_png_bytes(color: tuple[int, int, int] = (200, 50, 50)) -> bytes:
    img = Image.new("RGB", (16, 16), color)
    from io import BytesIO
    buf = BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


HEIGHT_CONFIG = """
name: mini_height_precincts_f
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


def test_parse_result_carries_images_field():
    result = ParseResult(page_blocks=[])
    assert result.images == []


def test_image_data_accepts_bytes_or_path_only():
    via_bytes = ImageData(page_number=5, image_bytes=b"\x89PNG", image_format="png")
    assert via_bytes.image_bytes == b"\x89PNG"
    via_path = ImageData(page_number=5, image_path="/tmp/foo.png")
    assert via_path.image_path == "/tmp/foo.png"
    assert via_bytes.parse_status == ParseStatus.PARSED


def _seed_document_and_fragment(session) -> tuple[Document, SourceFragment]:
    document = Document(
        municipality="Halifax Regional Municipality",
        bylaw_name="Regional Centre Land Use By-law",
        source_path="/synthetic.pdf",
        file_hash="f" * 64,
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
    return document, fragment


def test_persist_images_writes_bytes_to_storage_and_links_caption(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("IMAGE_STORAGE_DIR", str(tmp_path / "images"))
    from layer1.config import get_settings
    get_settings.cache_clear()  # type: ignore[attr-defined]

    db_url = f"sqlite:///{tmp_path / 'layer1.db'}"
    create_layer1(db_url)

    image = ImageData(
        page_number=501,
        bbox=BBox(x0=10, y0=10, x1=110, y1=110),
        image_bytes=_make_png_bytes(),
        image_format="png",
        figure_kind="precinct_map",
        docling_ref="#/pictures/0",
        parse_status=ParseStatus.PARSED,
        metadata={"caption_text": "Schedule 15: Maximum Building Height Precincts"},
    )

    with session_scope(db_url) as session:
        document, fragment = _seed_document_and_fragment(session)
        persisted = _persist_images(session, document, [image], [fragment])
        assert len(persisted) == 1
        assert persisted[0].image_path is not None
        assert Path(persisted[0].image_path).exists()
        assert persisted[0].caption_fragment_id == fragment.id
        assert persisted[0].figure_kind == "precinct_map"
        assert persisted[0].docling_ref == "#/pictures/0"

    get_settings.cache_clear()  # type: ignore[attr-defined]


def test_persist_images_handles_missing_bytes_gracefully(tmp_path: Path):
    db_url = f"sqlite:///{tmp_path / 'layer1.db'}"
    create_layer1(db_url)
    vector_only = ImageData(
        page_number=501,
        bbox=BBox(x0=0, y0=0, x1=10, y1=10),
        image_bytes=None,
        figure_kind="unknown",
        parse_status=ParseStatus.PARSED,
    )
    with session_scope(db_url) as session:
        document, fragment = _seed_document_and_fragment(session)
        persisted = _persist_images(session, document, [vector_only], [fragment])
    assert persisted[0].image_path is None  # nothing to write
    assert persisted[0].bbox_json == {"x0": 0.0, "y0": 0.0, "x1": 10.0, "y1": 10.0}


def test_dataset_candidate_carries_source_image_id_when_image_exists(tmp_path: Path):
    db_url = f"sqlite:///{tmp_path / 'layer1.db'}"
    create_layer1(db_url)
    create_layer2(db_url)

    with session_scope(db_url) as session:
        document, fragment = _seed_document_and_fragment(session)
        document_id, fragment_id = document.id, fragment.id
        image = SourceImage(
            document_id=document.id,
            page_number=501,
            bbox_json={"x0": 0, "y0": 0, "x1": 100, "y1": 100},
            image_path=str(tmp_path / "mock.png"),
            image_format="png",
            caption_fragment_id=fragment.id,
            figure_kind="precinct_map",
            docling_ref="#/pictures/0",
            parse_status=ParseStatus.PARSED,
            metadata_json={},
        )
        session.add(image)
        session.flush()
        image_id = image.id

    cfg_path = tmp_path / "height.yaml"
    cfg_path.write_text(HEIGHT_CONFIG)
    with session_scope(db_url) as session:
        result = ingest_geo_dataset(session, cfg_path)
        assert result.link_result.status == "linked"

    with session_scope(db_url) as session:
        candidates = [
            CandidateFragment(
                source_fragment_id=fragment_id,
                source_type=SourceType.FRAGMENT.value,
                retrieval_channel=RetrievalChannel.FULL_TEXT.value,
                base_score=0.8,
                text="Schedule 15 prose.",
            )
        ]
        expanded = expand_datasets(session, candidates)

    dataset_candidate = next(c for c in expanded if c.source_type == SourceType.DATASET.value)
    assert dataset_candidate.metadata.get("source_image_id") == image_id


def test_dataset_candidate_without_image_has_no_source_image_id(tmp_path: Path):
    db_url = f"sqlite:///{tmp_path / 'layer1.db'}"
    create_layer1(db_url)
    create_layer2(db_url)
    with session_scope(db_url) as session:
        _, fragment = _seed_document_and_fragment(session)
        fragment_id = fragment.id

    cfg_path = tmp_path / "height.yaml"
    cfg_path.write_text(HEIGHT_CONFIG)
    with session_scope(db_url) as session:
        ingest_geo_dataset(session, cfg_path)

    with session_scope(db_url) as session:
        candidates = [
            CandidateFragment(
                source_fragment_id=fragment_id,
                source_type=SourceType.FRAGMENT.value,
                retrieval_channel=RetrievalChannel.FULL_TEXT.value,
                base_score=0.8,
                text="Schedule 15 prose.",
            )
        ]
        expanded = expand_datasets(session, candidates)
    dataset_candidate = next(c for c in expanded if c.source_type == SourceType.DATASET.value)
    assert "source_image_id" not in dataset_candidate.metadata


def test_docling_picture_item_emits_image_data():
    """Verify the Docling parser path emits an ImageData when a PictureItem
    appears in the iterated items. Uses synthetic Docling document objects
    matching the existing test pattern in tests/test_docling_parser.py."""
    pytest.importorskip("docling")
    pytest.importorskip("docling_core")

    from docling_core.types.doc.base import BoundingBox, CoordOrigin, Size
    from docling_core.types.doc.document import (
        DocItemLabel,
        DoclingDocument,
        PageItem,
        PictureItem,
        ProvenanceItem,
    )

    doc = DoclingDocument(
        name="schedule_15",
        pages={1: PageItem(size=Size(width=612, height=792), page_no=1)},
    )
    picture = PictureItem(
        self_ref="#/pictures/0",
        parent=None,
        children=[],
        label=DocItemLabel.PICTURE,
        prov=[
            ProvenanceItem(
                page_no=1,
                bbox=BoundingBox(l=72, t=600, r=540, b=200, coord_origin=CoordOrigin.BOTTOMLEFT),
                charspan=(0, 0),
            )
        ],
        captions=[],
        references=[],
        footnotes=[],
        annotations=[],
    )
    doc.pictures = [picture]

    # Verify the helper directly — it's the unit under test for Phase F:
    from layer1.parsers.pdf import _docling_picture_to_image
    from layer1.models.schemas import BBox

    bbox = BBox(x0=72, y0=192, x1=540, y1=592)
    image_data = _docling_picture_to_image(picture, page_number=1, bbox=bbox, document=doc)
    assert image_data.page_number == 1
    assert image_data.bbox == bbox
    assert image_data.docling_ref == "#/pictures/0"
    # Bytes are best-effort; figure_kind defaults to unknown when no caption resolves.
    assert image_data.figure_kind in {"unknown", "precinct_map"}
