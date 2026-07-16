"""Generate a synthetic PDF fixture for the ABS-57 e2e spec.

Writes a small valid PDF to ``web/e2e/fixtures/submission-demo.pdf``
containing text with building attributes at known values so the stub
PDF extractor can find them.

Also seeds a submission row with source_type=pdf and low-confidence
attributes directly in the DB (bypassing the upload flow), so the
e2e spec can test the confirmation UI without depending on the
real PDF extractor. The seeded submission has:

* building_height_m = 9.5 (confidence 0.5)
* building_height_storeys = 3 (confidence 0.85)
* gross_floor_area_m2 = 450 (confidence 0.3)

Idempotent: re-runs overwrite the fixture and the DB row. Invoked
from the e2e spec's beforeAll or from global-setup.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from sqlalchemy import select

# Ensure src/ is importable
HERE = Path(__file__).resolve().parent
REPO_ROOT = HERE.parent
sys.path.insert(0, str(REPO_ROOT / "src"))

from layer1.db.session import session_scope  # noqa: E402
from layer2.compliance.db.models import (  # noqa: E402
    Parcel,
    Submission,
    SubmissionAttribute,
    SubmissionAttributeSource,
    SubmissionSourceType,
    SubmissionStatus,
)


TEST_PID = "E2E00100"

ATTRIBUTES = [
    {
        "attribute_key": "building_height_m",
        "value_json": {"value": 9.5, "unit": "m"},
        "confidence": 0.5,
        "source": SubmissionAttributeSource.EXTRACTED,
        "evidence_json": {"pdf_snippet": "Building height: 9.5 metres"},
    },
    {
        "attribute_key": "building_height_storeys",
        "value_json": {"value": 3, "unit": "storeys"},
        "confidence": 0.85,
        "source": SubmissionAttributeSource.EXTRACTED,
        "evidence_json": {"pdf_snippet": "Number of storeys: 3"},
    },
    {
        "attribute_key": "gross_floor_area_m2",
        "value_json": {"value": 450, "unit": "m2"},
        "confidence": 0.3,
        "source": SubmissionAttributeSource.EXTRACTED,
        "evidence_json": {"pdf_snippet": "Gross floor area: 450 m2"},
    },
]


def _write_fixture_pdf(out_path: Path) -> None:
    """Write a minimal valid PDF with building attribute text."""
    content = (
        "Development Application - Drawing Set\n\n"
        "Project: E2E Test Building\n"
        "Location: 100 Evaluator Way, Halifax\n\n"
        "Building height: 9.5 metres\n"
        "Number of storeys: 3\n"
        "Gross floor area: 450 m2\n"
        "Residential units: 6\n\n"
        "Zone: ER-1\n"
    )
    # Minimal PDF structure
    stream = content.encode("latin-1")
    stream_obj = (
        b"4 0 obj\n<< /Length " + str(len(stream)).encode() + b" >>\n"
        b"stream\n" + stream + b"\nendstream\nendobj\n"
    )

    objects = []
    objects.append(b"1 0 obj\n<< /Type /Catalog /Pages 2 0 R >>\nendobj\n")
    objects.append(
        b"2 0 obj\n<< /Type /Pages /Kids [3 0 R] /Count 1 >>\nendobj\n"
    )
    objects.append(
        b"3 0 obj\n<< /Type /Page /Parent 2 0 R "
        b"/MediaBox [0 0 612 792] "
        b"/Contents 4 0 R "
        b"/Resources << /Font << /F1 5 0 R >> >> "
        b">>\nendobj\n"
    )
    objects.append(stream_obj)
    objects.append(
        b"5 0 obj\n<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>\nendobj\n"
    )

    body = b"%PDF-1.4\n"
    offsets = []
    for obj in objects:
        offsets.append(len(body))
        body += obj

    xref_offset = len(body)
    xref = b"xref\n0 6\n"
    xref += b"0000000000 65535 f \n"
    for off in offsets:
        xref += f"{off:010d} 00000 n \n".encode()

    trailer = (
        b"trailer\n<< /Size 6 /Root 1 0 R >>\n"
        b"startxref\n" + str(xref_offset).encode() + b"\n%%EOF\n"
    )

    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_bytes(body + xref + trailer)


def seed_pdf_submission(session) -> int:
    """Insert a PDF submission with low-confidence attributes.

    Returns the submission id. Uses the same advisory lock as the
    evaluator seed to avoid parallel-worker races.
    """
    if session.bind.dialect.name == "postgresql":
        from sqlalchemy import text as sa_text
        session.execute(sa_text("SELECT pg_advisory_xact_lock(:k)").bindparams(k=2604601157))

    parcel = (
        session.execute(
            select(Parcel).where(
                Parcel.jurisdiction == "HRM",
                Parcel.parcel_identifier == TEST_PID,
            )
        )
        .scalars()
        .first()
    )
    if parcel is None:
        raise RuntimeError(
            f"Parcel {TEST_PID} not found — run seed_e2e_evaluator_bylaws.py first"
        )

    submission = Submission(
        parcel_id=parcel.id,
        submitter_id=1,
        status=SubmissionStatus.DRAFT,
        source_type=SubmissionSourceType.PDF,
        source_artifact_path="e2e/submission-demo.pdf",
        metadata_json={"extractor": {"name": "pdf-submission-stub"}},
    )
    session.add(submission)
    session.flush()

    for attr_spec in ATTRIBUTES:
        session.add(
            SubmissionAttribute(
                submission_id=submission.id,
                **attr_spec,
            )
        )
    session.flush()

    return submission.id


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Generate ABS-57 e2e PDF fixture + seed submission."
    )
    parser.add_argument(
        "--out",
        type=Path,
        default=REPO_ROOT / "web" / "e2e" / "fixtures" / "submission-demo.pdf",
    )
    parser.add_argument(
        "--skip-db",
        action="store_true",
        help="Only write the PDF file, skip DB seeding.",
    )
    args = parser.parse_args()

    _write_fixture_pdf(args.out)
    print(f"seed_e2e_submission_pdf: wrote {args.out} ({args.out.stat().st_size} bytes)")

    if not args.skip_db:
        with session_scope() as session:
            sid = seed_pdf_submission(session)
        print(f"seed_e2e_submission_pdf: seeded submission id={sid}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
