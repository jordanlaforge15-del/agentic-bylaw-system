"""Scaffold-level coverage for `ingest_submission`.

The real extractors (IFC, APS) land in ABS-49 / ABS-50; this suite
exercises the seam:

* the factory dispatches by `SubmissionSourceType`
* the pipeline persists `Submission` + `SubmissionAttribute` rows
* unknown taxonomy keys are dropped with a warning, not persisted
* the evaluator hook is gated by `config.run_evaluator` and the
  presence of an injected evaluator
* the derived-attribute hook (ABS-51) layers DERIVED rows on top of
  EXTRACTED ones
* manual overrides win over extracted values for the same key

All sqlite, in-process. No retrieval / evaluator stack imported.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from layer1.db.init_db import create_all
from layer1.db.session import session_scope
from layer1.models.submission_schemas import (
    ExtractedAttribute,
    SubmissionExtractionResult,
    SubmissionIngestConfig,
)
from layer1.parsers.submission_factory import (
    ExtractorNotRegisteredError,
    extract_submission,
    register_extractor,
    unregister_extractor,
)
from layer1.pipeline.ingest_submission import ingest_submission
from layer2.compliance.db.models import (
    Submission,
    SubmissionAttribute,
    SubmissionAttributeSource,
    SubmissionSourceType,
    SubmissionStatus,
)
from layer2.compliance.taxonomy import (
    AttributeCategory,
    AttributeDefinition,
    AttributeValueType,
    Taxonomy,
)


# ----------------------------------------------------------------------
# Fixtures
# ----------------------------------------------------------------------


@pytest.fixture()
def db_url(tmp_path: Path) -> str:
    url = f"sqlite:///{tmp_path / 'submissions.db'}"
    create_all(url)
    return url


@pytest.fixture()
def source_file(tmp_path: Path) -> Path:
    p = tmp_path / "model.ifc"
    p.write_text("ISO-10303-21;\n")
    return p


@pytest.fixture()
def stub_taxonomy() -> Taxonomy:
    """Two-key taxonomy: enough to test accept + reject paths."""
    return Taxonomy(
        version="test-taxonomy-1",
        attributes=(
            AttributeDefinition(
                id="building_height_m",
                category=AttributeCategory.GEOMETRIC_DIRECT,
                value_type=AttributeValueType.NUMBER,
                description="Building height in metres",
                unit="m",
            ),
            AttributeDefinition(
                id="gross_floor_area_m2",
                category=AttributeCategory.GEOMETRIC_DIRECT,
                value_type=AttributeValueType.NUMBER,
                description="GFA in square metres",
                unit="m2",
            ),
        ),
    )


def _make_stub_extractor(
    attrs: list[ExtractedAttribute],
    *,
    warnings: list[str] | None = None,
    footprint_geojson: dict[str, Any] | None = None,
):
    def _extract(source_path: Path, config: SubmissionIngestConfig):
        return SubmissionExtractionResult(
            source_type=SubmissionSourceType.IFC,
            source_artifact_path=str(source_path),
            attributes=attrs,
            warnings=warnings or [],
            footprint_geojson=footprint_geojson,
            raw_metadata={"library": "stub", "version": "0.0"},
        )

    return _extract


# ----------------------------------------------------------------------
# Factory tests
# ----------------------------------------------------------------------


def test_factory_dispatches_by_source_type(source_file: Path):
    sentinel = SubmissionExtractionResult(source_type=SubmissionSourceType.PDF)

    def _pdf(source_path: Path, config: SubmissionIngestConfig):
        return sentinel

    register_extractor(SubmissionSourceType.PDF, _pdf)
    try:
        result = extract_submission(
            source_file, SubmissionSourceType.PDF, config=SubmissionIngestConfig()
        )
    finally:
        unregister_extractor(SubmissionSourceType.PDF)

    assert result is sentinel


def test_factory_raises_when_no_extractor_registered(source_file: Path):
    # SPECKLE has no built-in extractor in the scaffold.
    with pytest.raises(ExtractorNotRegisteredError):
        extract_submission(
            source_file, SubmissionSourceType.SPECKLE, config=SubmissionIngestConfig()
        )


def test_factory_validates_source_path_exists(tmp_path: Path):
    missing = tmp_path / "nope.ifc"
    with pytest.raises(FileNotFoundError):
        extract_submission(
            missing, SubmissionSourceType.MANUAL, config=SubmissionIngestConfig()
        )


# ----------------------------------------------------------------------
# Pipeline tests
# ----------------------------------------------------------------------


def test_pipeline_persists_submission_and_attributes(
    db_url: str, source_file: Path, stub_taxonomy: Taxonomy
):
    extractor = _make_stub_extractor([
        ExtractedAttribute(
            attribute_key="building_height_m",
            value=12.5,
            unit="m",
            confidence=0.9,
            evidence={"ifc_global_id": "1abc"},
        ),
    ])

    with session_scope(db_url) as session:
        result = ingest_submission(
            session,
            source_file,
            SubmissionSourceType.IFC,
            config=SubmissionIngestConfig(run_evaluator=False),
            extractor=extractor,
            taxonomy=stub_taxonomy,
        )

    assert result.n_attributes_persisted == 1
    assert result.n_attributes_skipped == 0
    assert result.errors == []
    assert result.evaluator_response is None

    with session_scope(db_url) as session:
        sub = session.get(Submission, result.submission_id)
        assert sub is not None
        # No evaluator → status stays DRAFT.
        assert sub.status == SubmissionStatus.DRAFT
        assert sub.source_type == SubmissionSourceType.IFC
        attrs = list(sub.attributes)
        assert len(attrs) == 1
        row = attrs[0]
        assert row.attribute_key == "building_height_m"
        assert row.value_json == {"value": 12.5, "unit": "m"}
        assert row.source == SubmissionAttributeSource.EXTRACTED
        assert row.confidence == pytest.approx(0.9)
        assert row.evidence_json == {"ifc_global_id": "1abc"}


def test_pipeline_drops_unknown_taxonomy_keys_with_warning(
    db_url: str, source_file: Path, stub_taxonomy: Taxonomy
):
    extractor = _make_stub_extractor([
        ExtractedAttribute(attribute_key="building_height_m", value=10.0, unit="m"),
        ExtractedAttribute(attribute_key="totally_made_up_key", value=42),
    ])

    with session_scope(db_url) as session:
        result = ingest_submission(
            session,
            source_file,
            SubmissionSourceType.IFC,
            config=SubmissionIngestConfig(run_evaluator=False),
            extractor=extractor,
            taxonomy=stub_taxonomy,
        )

    assert result.n_attributes_persisted == 1
    assert result.n_attributes_skipped == 1
    assert any("totally_made_up_key" in w for w in result.warnings)

    with session_scope(db_url) as session:
        sub = session.get(Submission, result.submission_id)
        keys = {a.attribute_key for a in sub.attributes}
        assert keys == {"building_height_m"}


def test_pipeline_runs_evaluator_when_configured(
    db_url: str, source_file: Path, stub_taxonomy: Taxonomy
):
    captured: dict[str, Any] = {}

    class _Resp:
        approval_decision_id = 7

        def to_json(self) -> dict[str, Any]:
            return {"overall_status": "approved", "evaluator_version": "stub"}

    def _build_request(*, submission, attributes, config):
        captured["submission_id"] = submission.id
        captured["n_attrs"] = len(attributes)
        return {"submission_id": submission.id}

    def _evaluator(request: dict[str, Any]) -> _Resp:
        captured["request"] = request
        return _Resp()

    extractor = _make_stub_extractor([
        ExtractedAttribute(attribute_key="building_height_m", value=10.0, unit="m"),
    ])

    with session_scope(db_url) as session:
        result = ingest_submission(
            session,
            source_file,
            SubmissionSourceType.IFC,
            config=SubmissionIngestConfig(run_evaluator=True),
            extractor=extractor,
            evaluator=_evaluator,
            build_evaluation_request=_build_request,
            taxonomy=stub_taxonomy,
        )

    assert captured["n_attrs"] == 1
    assert result.evaluator_response == {
        "overall_status": "approved",
        "evaluator_version": "stub",
    }
    assert result.approval_decision_id == 7

    with session_scope(db_url) as session:
        sub = session.get(Submission, result.submission_id)
        assert sub.status == SubmissionStatus.EVALUATED


def test_pipeline_skips_evaluator_when_disabled(
    db_url: str, source_file: Path, stub_taxonomy: Taxonomy
):
    extractor = _make_stub_extractor([
        ExtractedAttribute(attribute_key="building_height_m", value=10.0, unit="m"),
    ])
    called: list[Any] = []

    def _evaluator(request):
        called.append(request)

    with session_scope(db_url) as session:
        result = ingest_submission(
            session,
            source_file,
            SubmissionSourceType.IFC,
            config=SubmissionIngestConfig(run_evaluator=False),
            extractor=extractor,
            evaluator=_evaluator,
            taxonomy=stub_taxonomy,
        )

    assert called == []
    assert result.evaluator_response is None
    with session_scope(db_url) as session:
        sub = session.get(Submission, result.submission_id)
        assert sub.status == SubmissionStatus.DRAFT


def test_manual_attributes_override_extracted(
    db_url: str, source_file: Path, stub_taxonomy: Taxonomy
):
    extractor = _make_stub_extractor([
        ExtractedAttribute(attribute_key="building_height_m", value=10.0, unit="m"),
        ExtractedAttribute(attribute_key="gross_floor_area_m2", value=500.0, unit="m2"),
    ])
    overrides = [
        ExtractedAttribute(
            attribute_key="building_height_m",
            value=11.0,
            unit="m",
            source=SubmissionAttributeSource.EXTRACTED,  # should be overwritten to MANUAL
        )
    ]

    with session_scope(db_url) as session:
        result = ingest_submission(
            session,
            source_file,
            SubmissionSourceType.IFC,
            config=SubmissionIngestConfig(run_evaluator=False),
            extractor=extractor,
            manual_attributes=overrides,
            taxonomy=stub_taxonomy,
        )

    assert result.n_attributes_persisted == 2
    with session_scope(db_url) as session:
        sub = session.get(Submission, result.submission_id)
        rows = {a.attribute_key: a for a in sub.attributes}
        assert rows["building_height_m"].value_json == {"value": 11.0, "unit": "m"}
        assert rows["building_height_m"].source == SubmissionAttributeSource.MANUAL
        assert rows["gross_floor_area_m2"].source == SubmissionAttributeSource.EXTRACTED


def test_derived_attribute_hook_layers_in_derived_rows(
    db_url: str, source_file: Path, stub_taxonomy: Taxonomy
):
    extractor = _make_stub_extractor(
        [
            ExtractedAttribute(
                attribute_key="building_height_m", value=10.0, unit="m"
            ),
        ],
        footprint_geojson={"type": "Polygon", "coordinates": [[]]},
    )

    def _derive(session, submission, extraction):
        # Simulate ABS-51 computing GFA from the footprint geometry.
        assert extraction.footprint_geojson is not None
        return [
            ExtractedAttribute(
                attribute_key="gross_floor_area_m2",
                value=420.0,
                unit="m2",
                confidence=0.8,
                evidence={"computed_from": "footprint"},
            )
        ]

    with session_scope(db_url) as session:
        result = ingest_submission(
            session,
            source_file,
            SubmissionSourceType.IFC,
            config=SubmissionIngestConfig(run_evaluator=False),
            extractor=extractor,
            derived_attribute_fn=_derive,
            taxonomy=stub_taxonomy,
        )

    assert result.n_attributes_persisted == 2
    with session_scope(db_url) as session:
        sub = session.get(Submission, result.submission_id)
        rows = {a.attribute_key: a for a in sub.attributes}
        assert rows["gross_floor_area_m2"].source == SubmissionAttributeSource.DERIVED
        # Footprint should be parked on metadata_json for downstream.
        assert sub.metadata_json["footprint_geojson"] == {
            "type": "Polygon",
            "coordinates": [[]],
        }


def test_pipeline_records_extractor_failure_without_raising(
    db_url: str, source_file: Path, stub_taxonomy: Taxonomy
):
    def _explode(source_path: Path, config: SubmissionIngestConfig):
        raise RuntimeError("ifcopenshell hated this file")

    with session_scope(db_url) as session:
        result = ingest_submission(
            session,
            source_file,
            SubmissionSourceType.IFC,
            config=SubmissionIngestConfig(run_evaluator=False),
            extractor=_explode,
            taxonomy=stub_taxonomy,
        )

    assert result.n_attributes_persisted == 0
    assert any("extractor failed" in e for e in result.errors)
    with session_scope(db_url) as session:
        sub = session.get(Submission, result.submission_id)
        # We still committed the row so the UI can show the failure.
        assert sub is not None
        assert sub.status == SubmissionStatus.DRAFT


def test_evaluator_failure_recorded_as_error_not_raised(
    db_url: str, source_file: Path, stub_taxonomy: Taxonomy
):
    extractor = _make_stub_extractor([
        ExtractedAttribute(attribute_key="building_height_m", value=10.0, unit="m"),
    ])

    def _evaluator(request):
        raise RuntimeError("retrieval down")

    with session_scope(db_url) as session:
        result = ingest_submission(
            session,
            source_file,
            SubmissionSourceType.IFC,
            config=SubmissionIngestConfig(run_evaluator=True),
            extractor=extractor,
            evaluator=_evaluator,
            build_evaluation_request=lambda **_: {},
            taxonomy=stub_taxonomy,
        )

    assert result.n_attributes_persisted == 1
    assert any("evaluator failed" in e for e in result.errors)
    with session_scope(db_url) as session:
        sub = session.get(Submission, result.submission_id)
        # Evaluator failed, status stays DRAFT (the extraction itself
        # succeeded so the attributes are kept).
        assert sub.status == SubmissionStatus.DRAFT
        assert len(list(sub.attributes)) == 1
