"""Phase H — structured LocationSlot on the MCP retrieval API.

These tests exercise the MCP retrieval surface (mcp/bylaw_retrieval/) rather
than Layer 2 retrieve_context. The MCP path NEVER invokes the regex extractor;
it only honours the structured ``location`` slot supplied by the caller.
"""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest

from bylaw_retrieval.openai_tools import build_openai_responses_tool_specs
from bylaw_retrieval.retrieval import (
    LocationSlot,
    RetrievalRequest,
    RetrievalService,
)
from layer1.db.base import Document, SourceFragment, SourceImage
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


@pytest.fixture()
def linked_dataset(tmp_path: Path):
    db_url = f"sqlite:///{tmp_path / 'mcp_h.db'}"
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
        session.flush()
        document_id, fragment_id = document.id, fragment.id

    cfg_path = tmp_path / "height.yaml"
    cfg_path.write_text(HEIGHT_CONFIG)
    with session_scope(db_url) as session:
        result = ingest_geo_dataset(session, cfg_path)
        assert result.link_result.status == "linked"
        dataset_id = result.dataset.id

    return {
        "db_url": db_url,
        "document_id": document_id,
        "fragment_id": fragment_id,
        "dataset_id": dataset_id,
    }


def test_search_without_location_returns_dataset_summary_only(linked_dataset):
    """Bulk-mode: no location, fragment match still surfaces its linked
    dataset as a summary so the caller can render the schedule generically."""
    with session_scope(linked_dataset["db_url"]) as session:
        service = RetrievalService(session)
        response = service.search(
            RetrievalRequest(
                query="Schedule 15 maximum building height precincts",
                document_id=linked_dataset["document_id"],
                limit=5,
            )
        )

    assert response.matches
    top = response.matches[0]
    assert top.fragment_id == linked_dataset["fragment_id"]
    assert len(top.linked_datasets) == 1
    ds = top.linked_datasets[0]
    assert ds.dataset_id == linked_dataset["dataset_id"]
    assert ds.feature_count == 3
    assert "Schedule 15" in ds.summary_text
    assert ds.feature_matches == []  # no spatial filtering without a location
    assert ds.location_resolver is None


def test_search_with_geometry_slot_skips_geocoding(linked_dataset):
    """Caller-supplied GeoJSON: spatial expansion runs without ever calling
    a geocoder. ResolvedLocation.source = 'caller_supplied' is the
    provenance signal."""
    with session_scope(linked_dataset["db_url"]) as session:
        service = RetrievalService(session)
        response = service.search(
            RetrievalRequest(
                query="Schedule 15",
                document_id=linked_dataset["document_id"],
                location=LocationSlot(
                    geometry={"type": "Point", "coordinates": [-63.59, 44.65]}
                ),
                limit=5,
            )
        )

    top = response.matches[0]
    assert len(top.linked_datasets) == 1
    ds = top.linked_datasets[0]
    assert ds.location_resolver == "caller_supplied"
    # Caller-supplied geometry implies maximum confidence — no geocoder
    # involved, no approximation. Surface the value so LLM consumers can
    # distinguish "I trust this" from "approximate, qualify the answer".
    assert ds.location_confidence == 1.0
    assert len(ds.feature_matches) == 1
    fm = ds.feature_matches[0]
    assert fm.canonical_attributes["max_height_m"] == 25.0
    assert fm.contains_input is True


def test_search_with_civic_address_slot_uses_layered_resolver(linked_dataset, monkeypatch):
    """Civic address slot routes through resolve_location. We inject a fake
    Google geocoder so this test stays hermetic."""
    from layer2.retrieval.google_geocoder import GoogleGeocoder, GoogleGeocoderConfig

    class _StubResponse:
        def json(self):
            return {
                "status": "OK",
                "results": [
                    {
                        "geometry": {
                            "location": {"lat": 44.65, "lng": -63.59},
                            "location_type": "ROOFTOP",
                        }
                    }
                ],
            }

    class _StubHttp:
        def get(self, url, *, params, timeout):
            return _StubResponse()

    fake_geocoder = GoogleGeocoder(
        GoogleGeocoderConfig(api_key="test-key"), http_client=_StubHttp()
    )
    import layer2.retrieval.geocode as geocode_module
    monkeypatch.setattr(geocode_module, "_maybe_build_google_geocoder", lambda: fake_geocoder)

    with session_scope(linked_dataset["db_url"]) as session:
        service = RetrievalService(session)
        response = service.search(
            RetrievalRequest(
                query="max height",
                document_id=linked_dataset["document_id"],
                location=LocationSlot(
                    civic_number="1234", street="Barrington Street"
                ),
                limit=5,
            )
        )

    top = response.matches[0]
    assert len(top.linked_datasets) == 1
    ds = top.linked_datasets[0]
    assert ds.location_resolver == "google_maps"
    # ROOFTOP-quality match -> 0.95 confidence per Phase G's mapping table.
    # Surfacing this lets an LLM distinguish a precise rooftop hit from a
    # RANGE_INTERPOLATED or GEOMETRIC_CENTER fallback.
    assert ds.location_confidence == 0.95
    assert len(ds.feature_matches) == 1
    assert ds.feature_matches[0].canonical_attributes["max_height_m"] == 25.0


def test_search_with_unresolvable_slot_returns_dataset_without_features(linked_dataset):
    """If the geocoder can't resolve the address, the fragment match still
    surfaces the dataset summary; feature_matches is just empty."""
    with session_scope(linked_dataset["db_url"]) as session:
        service = RetrievalService(session)
        response = service.search(
            RetrievalRequest(
                query="max height",
                document_id=linked_dataset["document_id"],
                location=LocationSlot(named_place="A Place That Does Not Exist"),
                limit=5,
            )
        )

    top = response.matches[0]
    assert len(top.linked_datasets) == 1
    ds = top.linked_datasets[0]
    assert ds.feature_matches == []
    assert ds.location_resolver is None  # geocoder returned None


def test_search_include_datasets_false_omits_linked_datasets(linked_dataset):
    """Existing clients that don't know about linked_datasets can opt out."""
    with session_scope(linked_dataset["db_url"]) as session:
        service = RetrievalService(session)
        response = service.search(
            RetrievalRequest(
                query="Schedule 15",
                document_id=linked_dataset["document_id"],
                include_datasets=False,
                limit=5,
            )
        )
    top = response.matches[0]
    assert top.linked_datasets == []


def test_search_attaches_source_image_id_when_present(linked_dataset):
    """When a SourceImage is captioned by the linked fragment, LinkedDataset
    surfaces source_image_id so a UI can render the legally enacted map."""
    with session_scope(linked_dataset["db_url"]) as session:
        image = SourceImage(
            document_id=linked_dataset["document_id"],
            page_number=501,
            bbox_json={"x0": 0, "y0": 0, "x1": 100, "y1": 100},
            image_path="/tmp/mock.png",
            image_format="png",
            caption_fragment_id=linked_dataset["fragment_id"],
            figure_kind="precinct_map",
            docling_ref="#/pictures/0",
            parse_status=ParseStatus.PARSED,
            metadata_json={},
        )
        session.add(image)
        session.flush()
        image_id = image.id

    with session_scope(linked_dataset["db_url"]) as session:
        service = RetrievalService(session)
        response = service.search(
            RetrievalRequest(
                query="Schedule 15",
                document_id=linked_dataset["document_id"],
                limit=5,
            )
        )
    top = response.matches[0]
    assert top.linked_datasets[0].source_image_id == image_id


def test_intersection_slot_resolves_via_two_streets(linked_dataset, monkeypatch):
    """Intersection slot serializes as 'street1 and street2' for the
    geocoder. Stub the Google fallback to assert the params went through."""
    from layer2.retrieval.google_geocoder import GoogleGeocoder, GoogleGeocoderConfig

    captured: dict = {}

    class _StubResponse:
        def json(self):
            return {
                "status": "OK",
                "results": [
                    {
                        "geometry": {
                            "location": {"lat": 44.65, "lng": -63.59},
                            "location_type": "GEOMETRIC_CENTER",
                        }
                    }
                ],
            }

    class _StubHttp:
        def get(self, url, *, params, timeout):
            captured.update(params)
            return _StubResponse()

    fake = GoogleGeocoder(GoogleGeocoderConfig(api_key="x"), http_client=_StubHttp())
    import layer2.retrieval.geocode as geocode_module
    monkeypatch.setattr(geocode_module, "_maybe_build_google_geocoder", lambda: fake)

    with session_scope(linked_dataset["db_url"]) as session:
        service = RetrievalService(session)
        service.search(
            RetrievalRequest(
                query="height",
                document_id=linked_dataset["document_id"],
                location=LocationSlot(
                    intersection_streets=["Barrington Street", "Spring Garden Road"]
                ),
                limit=5,
            )
        )
    assert "Barrington Street and Spring Garden Road" in captured.get("address", "")


def test_openai_tool_spec_advertises_location_slot():
    """The LLM tool description must tell callers to populate location
    rather than embedding addresses in 'query'. This is what makes the
    structured-slot pattern actually get used in production."""
    specs = build_openai_responses_tool_specs()
    search_spec = next(spec for spec in specs if spec["name"] == "search_bylaw_evidence")
    assert "location" in search_spec["parameters"]["properties"]
    location_schema = search_spec["parameters"]["properties"]["location"]
    assert location_schema["type"] == "object"
    expected_fields = {
        "civic_number",
        "street",
        "unit",
        "parcel_id",
        "named_place",
        "intersection_streets",
        "geometry",
    }
    assert expected_fields <= set(location_schema["properties"].keys())
    description = search_spec["description"].lower()
    assert "location" in description
    assert "address" in description or "parcel" in description


def test_openai_executor_accepts_location_payload(linked_dataset):
    """End-to-end: an LLM-style tool call with a location payload routes
    through the OpenAI executor and returns the spatial match."""
    from bylaw_retrieval.openai_tools import OpenAIToolExecutor

    with session_scope(linked_dataset["db_url"]) as session:
        executor = OpenAIToolExecutor(session=session)
        result = executor.execute(
            "search_bylaw_evidence",
            {
                "query": "max height",
                "document_id": linked_dataset["document_id"],
                "location": {
                    "geometry": {"type": "Point", "coordinates": [-63.59, 44.65]}
                },
                "limit": 5,
            },
        )

    matches = result["matches"]
    assert matches
    linked = matches[0]["linked_datasets"]
    assert linked
    assert linked[0]["feature_matches"]
    assert linked[0]["feature_matches"][0]["canonical_attributes"]["max_height_m"] == 25.0
