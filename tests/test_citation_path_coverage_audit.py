"""Coverage for the citation-path coverage audit (ABS-461, DoD 6).

The audit's whole job is to split "no path" into "correctly has no path" and
"is a citable provision that nothing can reach". If that split drifts, the
number in docs/data-gaps/citation-path-coverage.md stops meaning anything.
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

from layer1.db.base import Document, SourceFragment
from layer1.db.init_db import create_all
from layer1.db.session import session_scope
from layer1.models.enums import FragmentType, ParseStatus

_SCRIPT_PATH = Path(__file__).resolve().parent.parent / "scripts" / "audit_citation_path_coverage.py"
_spec = importlib.util.spec_from_file_location("audit_citation_path_coverage", _SCRIPT_PATH)
assert _spec is not None and _spec.loader is not None
_module = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = _module
_spec.loader.exec_module(_module)

audit = _module.audit

COLLIDED = "Part I > 9 > [Development Permit Exemptions] > (a)"


def _seed(db_url: str) -> int:
    create_all(db_url)
    with session_scope(db_url) as session:
        document = Document(
            municipality="HRM",
            bylaw_name="Regional Centre Land Use By-Law",
            source_path="rc.pdf",
            file_hash="abs461-coverage-audit",
            mime_type="application/pdf",
            page_count=457,
            parser_version="docling:halifax",
        )
        session.add(document)
        session.flush()

        rows = [
            # pathed
            (FragmentType.SECTION, "9", "Part I > 9", {}, "9 Subject to..."),
            (FragmentType.CLAUSE, "(c)", "Part I > 9 > (c)", {}, "(c) something."),
            # legitimately unpathed: no label
            (FragmentType.HEADING, None, None, {}, "Development Permit Exemptions"),
            (FragmentType.PROSE, None, None, {}, "Body prose with no number."),
            (FragmentType.LIST_ITEM, None, None, {}, "An unnumbered list item."),
            # citable but missing: collision
            (FragmentType.CLAUSE, "(a)", None, {"duplicate_citation_path": COLLIDED}, "(a) first."),
            (FragmentType.CLAUSE, "(a)", None, {"duplicate_citation_path": COLLIDED}, "(a) second."),
            (FragmentType.PART, "Part I", None, {"duplicate_citation_path": "Part I"}, "Part I, Chapter 1"),
            # citable but missing: labelled, no path, no recorded collision
            (FragmentType.CLAUSE, "(z)", None, {}, "(z) an orphan clause."),
        ]
        for order, (kind, label, path, metadata, text) in enumerate(rows):
            session.add(
                SourceFragment(
                    document_id=document.id,
                    fragment_type=kind,
                    citation_label=label,
                    citation_path=path,
                    page_start=1,
                    page_end=1,
                    reading_order_start=order,
                    reading_order_end=order,
                    text=text,
                    parse_status=ParseStatus.PARSED,
                    confidence=0.9,
                    source_block_ids_json=[order],
                    metadata_json=metadata,
                )
            )
        return document.id


def test_audit_splits_unpathed_into_legitimate_and_citable(tmp_path):
    db_url = f"sqlite:///{tmp_path / 'audit.db'}"
    document_id = _seed(db_url)

    with session_scope(db_url) as session:
        report = audit(session, document_id=document_id)

    assert (report.total, report.pathed, report.unpathed) == (9, 2, 7)

    # No label -> correctly unpathed, whatever the fragment type.
    assert dict(report.unlabelled) == {"heading": 1, "prose": 1, "list_item": 1}

    # A recorded duplicate_citation_path means the provision is citable and the
    # collision rule took its path away.
    assert dict(report.collision) == {"clause": 2, "part": 1}
    assert report.collided_paths[COLLIDED] == 2

    # Labelled but never pathed at all is the third class.
    assert dict(report.unresolved) == {"clause": 1}

    assert report.citable_but_missing == 4


def test_a_fully_pathed_document_reports_no_gap(tmp_path):
    db_url = f"sqlite:///{tmp_path / 'audit.db'}"
    create_all(db_url)
    with session_scope(db_url) as session:
        document = Document(
            municipality="HRM",
            bylaw_name="Clean Bylaw",
            source_path="clean.pdf",
            file_hash="abs461-clean",
            mime_type="application/pdf",
            page_count=1,
            parser_version="test",
        )
        session.add(document)
        session.flush()
        session.add(
            SourceFragment(
                document_id=document.id,
                fragment_type=FragmentType.SECTION,
                citation_label="1",
                citation_path="Part I > 1",
                page_start=1,
                page_end=1,
                reading_order_start=0,
                reading_order_end=0,
                text="1 The only section.",
                parse_status=ParseStatus.PARSED,
                confidence=1.0,
                source_block_ids_json=[0],
                metadata_json={},
            )
        )
        document_id = document.id

    with session_scope(db_url) as session:
        report = audit(session, document_id=document_id)

    assert report.unpathed == 0
    assert report.citable_but_missing == 0
