"""On-disk persistence for :class:`CityIntakeManifest` artifacts.

The City Learning Pipeline is a learn-time producer; Layer 1 ingestion is a
consumer that runs in a separate process at a later time. The two stages
exchange state through one place on disk:

    {root}/{jurisdiction_code}/manifest.json

``root`` defaults to ``abs-learning/output`` (relative to the repository root)
because that path is what the parent issue (ABS-74) called out as the simplest
workable choice. Callers that want a different location pass ``root``
explicitly — the tests do this so they don't pollute the tracked tree.

The format is the canonical pydantic JSON dump of ``CityIntakeManifest``;
``load_manifest`` re-validates the file so a hand-edited or stale artifact
fails at load time rather than partway through Layer 1.
"""
from __future__ import annotations

from pathlib import Path

from manifest.models import CityIntakeManifest


DEFAULT_OUTPUT_ROOT = Path("abs-learning/output")
MANIFEST_FILENAME = "manifest.json"


def manifest_path(jurisdiction_code: str, root: Path | str | None = None) -> Path:
    """Return the on-disk path where a manifest for ``jurisdiction_code`` lives."""
    base = Path(root) if root is not None else DEFAULT_OUTPUT_ROOT
    return base / jurisdiction_code / MANIFEST_FILENAME


def save_manifest(
    manifest: CityIntakeManifest,
    root: Path | str | None = None,
) -> Path:
    """Serialize ``manifest`` to ``{root}/{jurisdiction_code}/manifest.json``.

    Returns the path written. Overwrites any existing file at that location.
    """
    target = manifest_path(manifest.municipality.jurisdiction_code, root)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(manifest.model_dump_json(indent=2), encoding="utf-8")
    return target


def load_manifest(path: Path | str) -> CityIntakeManifest:
    """Load and validate a manifest JSON file from ``path``."""
    return CityIntakeManifest.model_validate_json(Path(path).read_text(encoding="utf-8"))


def load_manifest_for_jurisdiction(
    jurisdiction_code: str,
    root: Path | str | None = None,
) -> CityIntakeManifest:
    """Load the manifest for ``jurisdiction_code`` from the conventional location."""
    return load_manifest(manifest_path(jurisdiction_code, root))
