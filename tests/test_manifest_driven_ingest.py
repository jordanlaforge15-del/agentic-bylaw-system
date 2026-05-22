"""Manifest-driven ingest matches the hardcoded-profile baseline (ABS-74).

This is the acceptance #2 test from the issue:

> Halifax baseline re-ingest matches the existing hardcoded path within
> tolerance.

We don't re-ingest the full 457-page Halifax PDF here — that would couple the
test to a binary outside the repo and take minutes. Instead we ingest a
synthetic Halifax-flavoured corpus twice in the same process:

1. With the historical ``HALIFAX_PROFILE`` selected by name.
2. With a manifest-derived profile produced by
   :func:`layer1.manifest_adapter.profile_from_manifest`, loaded from the
   committed ``abs-learning/output/halifax-regional-centre/manifest.json``.

Both runs use the same fixture and the same ingestion entrypoint. Their
fragment trees must agree on:

- total fragment count (exact)
- citation paths produced for every PARSED fragment (exact set equality)
- citation labels (exact, in document order)

If any of those drift, the manifest fixture has diverged from the hardcoded
behavior — which means either the manifest is wrong or the adapter regressed.
Either way the test fails loudly with the diff.

For acceptance #1 ("a second city ingested via the adapter with no hand-
edited profile") we run the *same* manifest path on the same fixture but
substitute a small Sampleton-flavoured manifest, asserting non-zero fragments
+ a part/section structure that matches what the manifest declared.
"""
from __future__ import annotations

import json
from pathlib import Path

from layer1.db.base import Document, SourceFragment
from layer1.db.init_db import create_all
from layer1.db.session import session_scope
from layer1.manifest_adapter import (
    load_manifest,
    profile_from_manifest,
)
from layer1.models.enums import IngestionStatus, ParseStatus
from layer1.pipeline.ingest import ingest_file
from layer1.profiles import HALIFAX_PROFILE


FIXTURE = Path(__file__).resolve().parent / "fixtures" / "synthetic_bylaw.txt"
HALIFAX_MANIFEST_PATH = (
    Path(__file__).resolve().parents[1]
    / "abs-learning"
    / "output"
    / "halifax-regional-centre"
    / "manifest.json"
)


def _ingest_and_dump_fragments(
    db_url: str, profile, *, municipality: str
) -> list[dict]:
    create_all(db_url)
    with session_scope(db_url) as session:
        document, run = ingest_file(
            session,
            FIXTURE,
            municipality=municipality,
            bylaw_name="Halifax Tolerance Synthetic",
            profile=profile,
        )
        assert run.status in {
            IngestionStatus.COMPLETED,
            IngestionStatus.COMPLETED_WITH_WARNINGS,
        }, run.errors_json
        rows = (
            session.query(SourceFragment)
            .filter_by(document_id=document.id)
            .order_by(SourceFragment.reading_order_start, SourceFragment.id)
            .all()
        )
        return [
            {
                "fragment_type": frag.fragment_type.value,
                "citation_label": frag.citation_label,
                "citation_path": frag.citation_path,
                "parse_status": frag.parse_status.value,
            }
            for frag in rows
        ]


def test_halifax_manifest_driven_ingest_matches_hardcoded_profile(tmp_path: Path):
    """Acceptance #2: manifest-driven Halifax ingest matches hardcoded within tolerance.

    Tolerance for this synthetic corpus: exact equality on fragment count,
    citation labels, and citation paths. The manifest patterns mirror the
    hardcoded ones, so any drift here means the wiring or mapping is wrong.
    """
    manifest = load_manifest(HALIFAX_MANIFEST_PATH)
    manifest_profile = profile_from_manifest(manifest, base=HALIFAX_PROFILE)

    hardcoded_db = f"sqlite:///{tmp_path / 'hardcoded.db'}"
    manifest_db = f"sqlite:///{tmp_path / 'manifest.db'}"

    hardcoded = _ingest_and_dump_fragments(
        hardcoded_db, HALIFAX_PROFILE, municipality="Halifax (hardcoded)"
    )
    manifest_run = _ingest_and_dump_fragments(
        manifest_db, manifest_profile, municipality="Halifax (manifest)"
    )

    assert len(hardcoded) == len(manifest_run), (
        f"Fragment count drifted: hardcoded={len(hardcoded)} "
        f"manifest={len(manifest_run)}"
    )
    for idx, (left, right) in enumerate(zip(hardcoded, manifest_run)):
        assert left["citation_label"] == right["citation_label"], (
            f"Citation label diverged at index {idx}: "
            f"hardcoded={left['citation_label']!r} manifest={right['citation_label']!r}"
        )
        assert left["citation_path"] == right["citation_path"], (
            f"Citation path diverged at index {idx}: "
            f"hardcoded={left['citation_path']!r} manifest={right['citation_path']!r}"
        )
        assert left["fragment_type"] == right["fragment_type"], (
            f"Fragment type diverged at index {idx}: "
            f"hardcoded={left['fragment_type']} manifest={right['fragment_type']}"
        )


def test_second_city_ingest_works_with_no_hand_edited_profile(tmp_path: Path):
    """Acceptance #1: a non-Halifax city ingests via manifest + adapter only.

    We synthesize a tiny Sampleton manifest (different zone codes, different
    citation prefixes) and assert the resulting ingest produces a non-empty
    fragment tree and finds the manifest's PART prefix in the corpus.
    """
    sampleton_payload = {
        "municipality": {
            "name": "Town of Sampleton",
            "jurisdiction_code": "sampleton",
            "province": "NS",
            "governing_body": "Sampleton Council",
        },
        "sources": [
            {
                "document_name": "Sampleton Zoning Bylaw",
                "document_type": "bylaw",
                "document_role": "Primary zoning bylaw",
                "in_scope": True,
            }
        ],
        "parser_config": {
            "parser_version": "synthetic-sampleton",
            "citation_scheme": {
                "full_citation_example": "Part 1, Section 1.1",
                "separator": ", ",
                "hierarchy": [
                    {
                        "level": "part",
                        "pattern": r"^\s*part\s+(\d+)",
                        "label_format": "Part {n}",
                    },
                    {
                        "level": "schedule",
                        "pattern": r"^\s*schedule\s+([A-Z]|\d+)",
                        "label_format": "Schedule {n}",
                    },
                    {
                        "level": "section",
                        "pattern": r"^\s*(\d+(?:\.\d+){0,5})\b",
                        "label_format": "{n}",
                    },
                ],
            },
            "schedule_patterns": [r"^\s*schedule\s+([A-Z]|\d+)"],
            "table_caption_pattern": r"^Table\s+\d+",
            "confidence": 0.9,
            "flags": [],
        },
        "taxonomy": {
            "zone_designations": [
                {"code": "R1", "canonical_type": "residential"},
                {"code": "C1", "canonical_type": "commercial"},
            ],
            "use_class_map": {
                "single-detached dwelling": "single_family_residential",
            },
            "standards_categories": ["lot area"],
            "companion_bylaws_required": [],
            "confidence": 0.85,
            "flags": [],
        },
        "qa_report": {
            "status": "PASS",
            "citation_resolution_rate": 0.9,
            "zone_completeness": 0.9,
            "pattern_coverage": 0.9,
            "flags": [],
            "recommended_action": "approve",
        },
        "manifest_version": "0.1.0",
        "status": "active",
        "pipeline_ready": True,
        "flags": [],
    }
    manifest_root = tmp_path / "manifests"
    out_dir = manifest_root / "sampleton"
    out_dir.mkdir(parents=True)
    (out_dir / "manifest.json").write_text(json.dumps(sampleton_payload), encoding="utf-8")

    manifest = load_manifest(out_dir / "manifest.json")
    profile = profile_from_manifest(manifest)
    assert profile.jurisdiction_code == "sampleton"
    assert profile.known_zone_codes == frozenset({"R1", "C1"})

    db_url = f"sqlite:///{tmp_path / 'sampleton.db'}"
    fragments = _ingest_and_dump_fragments(
        db_url, profile, municipality="Sampleton"
    )
    assert len(fragments) > 0
    parts = [f for f in fragments if f["fragment_type"] == "part" and f["parse_status"] == "parsed"]
    assert parts, (
        "Expected at least one PART fragment in Sampleton manifest-driven ingest "
        f"but got: {fragments[:5]}"
    )
    assert any(f["citation_label"] == "Part 1" for f in parts)
