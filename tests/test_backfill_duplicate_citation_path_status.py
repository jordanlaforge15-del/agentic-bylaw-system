"""Coverage for the ABS-480 collision-demotion backfill.

The backfill's whole job is to restore PARSED on rows the old collision sweep
demoted *and nothing else*. A rule that over-reaches silently promotes rows
that are uncertain for real reasons, which is worse than leaving the corpus
alone — so every skip class gets a row here.
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

from layer1.db.base import Document, SourceFragment
from layer1.db.init_db import create_all
from layer1.db.session import session_scope
from layer1.models.enums import FragmentType, ParseStatus

_SCRIPT_PATH = (
    Path(__file__).resolve().parent.parent
    / "scripts"
    / "backfill_duplicate_citation_path_status.py"
)
_spec = importlib.util.spec_from_file_location(
    "backfill_duplicate_citation_path_status", _SCRIPT_PATH
)
assert _spec is not None and _spec.loader is not None
_module = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = _module
_spec.loader.exec_module(_module)

backfill = _module.backfill

COLLIDED = "26 > (g)"

# (key, fragment_type, label, text, parse_status, confidence, metadata)
_ROWS = (
    # Demoted purely by the collision: the restore target. Text is a real
    # "(g) ..." clause line, so parse_citation_label re-derives 0.8.
    (
        "collided_first",
        FragmentType.CLAUSE,
        "(g)",
        "(g) First clause under section 26.",
        ParseStatus.UNCERTAIN,
        0.6,
        {"duplicate_citation_path": COLLIDED},
    ),
    (
        "collided_second",
        FragmentType.CLAUSE,
        "(g)",
        "(g) Second clause under section 26.",
        ParseStatus.UNCERTAIN,
        0.6,
        {"duplicate_citation_path": COLLIDED},
    ),
    # Already repaired (or ingested after the code fix): idempotence.
    (
        "already_parsed",
        FragmentType.CLAUSE,
        "(h)",
        "(h) A clause whose collision was already repaired.",
        ParseStatus.PARSED,
        0.8,
        {"duplicate_citation_path": "26 > (h)"},
    ),
    # Uncertain for a reason of its own — must not be touched.
    (
        "other_marker",
        FragmentType.CLAUSE,
        "(i)",
        "(i) An unaccounted block the fallback swept up.",
        ParseStatus.UNCERTAIN,
        0.4,
        {"duplicate_citation_path": "26 > (i)", "fallback_unaccounted_block": True},
    ),
    # Marker on a row the labelled-match branch could never have produced.
    (
        "unlabelled_type",
        FragmentType.PROSE,
        "(j)",
        "Body prose that somehow carries a collision marker.",
        ParseStatus.UNCERTAIN,
        0.5,
        {"duplicate_citation_path": "26 > (j)"},
    ),
    (
        "no_label",
        FragmentType.CLAUSE,
        None,
        "A clause row with no citation_label at all.",
        ParseStatus.UNCERTAIN,
        0.6,
        {"duplicate_citation_path": "26 > (k)"},
    ),
    # No marker: uncertain for an unrelated reason, out of scope entirely.
    (
        "no_marker",
        FragmentType.CLAUSE,
        "(l)",
        "(l) An orphan clause with no addressable ancestor.",
        ParseStatus.UNCERTAIN,
        0.6,
        {},
    ),
)


def _seed(db_url: str) -> dict[str, int]:
    create_all(db_url)
    ids: dict[str, int] = {}
    with session_scope(db_url) as session:
        document = Document(
            municipality="HRM",
            bylaw_name="Collision Bylaw",
            source_path="collision.pdf",
            file_hash="abs480-backfill",
            mime_type="application/pdf",
            page_count=1,
            parser_version="test",
        )
        session.add(document)
        session.flush()
        for order, (key, kind, label, text, status, confidence, metadata) in enumerate(_ROWS):
            fragment = SourceFragment(
                document_id=document.id,
                fragment_type=kind,
                citation_label=label,
                citation_path=None,
                page_start=1,
                page_end=1,
                reading_order_start=order,
                reading_order_end=order,
                text=text,
                parse_status=status,
                confidence=confidence,
                source_block_ids_json=[order],
                metadata_json=metadata,
            )
            session.add(fragment)
            session.flush()
            ids[key] = fragment.id
    return ids


def _statuses(db_url: str, ids: dict[str, int]) -> dict[str, tuple[ParseStatus, float | None]]:
    with session_scope(db_url) as session:
        return {
            key: (
                session.get(SourceFragment, fragment_id).parse_status,
                session.get(SourceFragment, fragment_id).confidence,
            )
            for key, fragment_id in ids.items()
        }


def test_dry_run_counts_the_restore_set_without_writing(tmp_path):
    db_url = f"sqlite:///{tmp_path / 'backfill.db'}"
    ids = _seed(db_url)
    before = _statuses(db_url, ids)

    with session_scope(db_url) as session:
        stats = backfill(session, dry_run=True)
        session.rollback()

    # Every row carrying the marker is in scope; the two collided clauses are
    # the only ones the rule restores.
    assert stats.fragments_with_marker == 6
    assert stats.restored_status == 2
    assert stats.already_parsed == 1
    assert dict(stats.skipped) == {
        "other_marker_fallback_unaccounted_block": 1,
        "unlabelled_fragment_type": 1,
        "no_citation_label": 1,
    }
    assert _statuses(db_url, ids) == before


def test_apply_restores_only_the_collision_demoted_rows(tmp_path):
    db_url = f"sqlite:///{tmp_path / 'backfill.db'}"
    ids = _seed(db_url)

    with session_scope(db_url) as session:
        stats = backfill(session, dry_run=False)

    assert stats.restored_status == 2
    after = _statuses(db_url, ids)

    # Restored: status flips, and the capped confidence is re-derived from the
    # clause pattern the parser originally matched (0.8, not the 0.6 cap).
    for key in ("collided_first", "collided_second"):
        assert after[key][0] == ParseStatus.PARSED
        assert after[key][1] is not None and after[key][1] > 0.6

    # Untouched: everything the rule declined.
    assert after["already_parsed"] == (ParseStatus.PARSED, 0.8)
    assert after["other_marker"] == (ParseStatus.UNCERTAIN, 0.4)
    assert after["unlabelled_type"] == (ParseStatus.UNCERTAIN, 0.5)
    assert after["no_label"] == (ParseStatus.UNCERTAIN, 0.6)
    assert after["no_marker"] == (ParseStatus.UNCERTAIN, 0.6)


def test_backfill_is_idempotent(tmp_path):
    db_url = f"sqlite:///{tmp_path / 'backfill.db'}"
    _seed(db_url)

    with session_scope(db_url) as session:
        backfill(session, dry_run=False)
    with session_scope(db_url) as session:
        second = backfill(session, dry_run=False)

    assert second.restored_status == 0
    assert second.confidence_restored == 0
    assert second.already_parsed == 3


def test_status_only_leaves_confidence_capped(tmp_path):
    db_url = f"sqlite:///{tmp_path / 'backfill.db'}"
    ids = _seed(db_url)

    with session_scope(db_url) as session:
        stats = backfill(session, dry_run=False, status_only=True)

    assert stats.restored_status == 2
    assert stats.confidence_restored == 0
    after = _statuses(db_url, ids)
    assert after["collided_first"] == (ParseStatus.PARSED, 0.6)


def test_document_scope_limits_the_sweep(tmp_path):
    db_url = f"sqlite:///{tmp_path / 'backfill.db'}"
    _seed(db_url)

    with session_scope(db_url) as session:
        # No document has this id, so nothing is in scope.
        stats = backfill(session, dry_run=True, document_id=9999)

    assert stats.fragments_with_marker == 0
    assert stats.restored_status == 0
