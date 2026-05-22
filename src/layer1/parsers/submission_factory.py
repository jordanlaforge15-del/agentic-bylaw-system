"""Dispatcher for submission-source extractors.

Parallel to `layer1.parsers.factory.parse_source` for the bylaw side.
Each `SubmissionSourceType` maps to a callable that turns a source
path into a `SubmissionExtractionResult`. The pipeline calls
`extract_submission(...)` rather than importing the extractors
directly so:

* ABS-49 (IFC) and ABS-50 (APS) plug new extractors in by calling
  `register_extractor(...)` from their module; no edit to the pipeline.
* Tests inject a synthetic extractor via the `extractor=` override on
  the pipeline, leaving the registry alone.

The registry is a process-global dict — same pattern as
`layer1.parsers.factory` — because extractors are stateless functions
keyed on an enum. No threading concern: registrations happen at module
import time before any concurrent ingest can run.
"""
from __future__ import annotations

from pathlib import Path
from typing import Callable

from layer1.models.submission_schemas import (
    SubmissionExtractionResult,
    SubmissionIngestConfig,
)
from layer2.compliance.db.models import SubmissionSourceType


Extractor = Callable[[Path, SubmissionIngestConfig], SubmissionExtractionResult]


class ExtractorNotRegisteredError(RuntimeError):
    """Raised when no extractor is registered for a SubmissionSourceType.

    ABS-49 / ABS-50 / future phases register concrete extractors at
    import time; before they land the pipeline raises this so callers
    fail loudly rather than silently producing empty submissions.
    """


_REGISTRY: dict[SubmissionSourceType, Extractor] = {}


def register_extractor(
    source_type: SubmissionSourceType, extractor: Extractor
) -> None:
    """Register an extractor for `source_type`.

    Subsequent registrations for the same source_type overwrite the
    previous one; this is intentional so tests can swap in a stub
    without monkeypatching the module dict.
    """
    _REGISTRY[source_type] = extractor


def unregister_extractor(source_type: SubmissionSourceType) -> None:
    """Drop the extractor for `source_type` (test cleanup helper)."""
    _REGISTRY.pop(source_type, None)


def get_extractor(source_type: SubmissionSourceType) -> Extractor:
    """Lookup the registered extractor or raise."""
    try:
        return _REGISTRY[source_type]
    except KeyError as exc:
        raise ExtractorNotRegisteredError(
            f"no extractor registered for SubmissionSourceType.{source_type.name} — "
            "register one via layer1.parsers.submission_factory.register_extractor "
            "(IFC: ABS-49, RVT_APS: ABS-50)"
        ) from exc


def extract_submission(
    source_path: Path,
    source_type: SubmissionSourceType,
    *,
    config: SubmissionIngestConfig,
) -> SubmissionExtractionResult:
    """Dispatch to the registered extractor for `source_type`.

    Raises `FileNotFoundError` before dispatch (cheap pre-flight; saves
    every extractor from re-implementing the same check).
    """
    source_path = source_path.resolve()
    if not source_path.exists():
        raise FileNotFoundError(source_path)
    extractor = get_extractor(source_type)
    return extractor(source_path, config)


# ----------------------------------------------------------------------
# Built-in extractors
# ----------------------------------------------------------------------


def _manual_extractor(
    source_path: Path, config: SubmissionIngestConfig
) -> SubmissionExtractionResult:
    """Stub for the MANUAL source_type.

    Manual submissions are advisor-UI-driven: attributes arrive via
    the UI's form, not by parsing the artifact. The pipeline still
    accepts a source_path (e.g. a reference PDF the architect uploaded
    for context) and persists the row; the empty attribute list here
    is the seam where the UI later layers its inline-typed
    attributes via the `manual_attributes=` ingest override.
    """
    return SubmissionExtractionResult(
        source_type=SubmissionSourceType.MANUAL,
        source_artifact_path=str(source_path),
        attributes=[],
        raw_metadata={"extractor": "manual-stub"},
        warnings=[],
    )


register_extractor(SubmissionSourceType.MANUAL, _manual_extractor)


__all__ = [
    "Extractor",
    "ExtractorNotRegisteredError",
    "extract_submission",
    "get_extractor",
    "register_extractor",
    "unregister_extractor",
]
