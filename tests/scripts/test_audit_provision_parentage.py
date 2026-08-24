"""The ABS-521 blast-radius measurement has to be right to be worth quoting.

``scripts/audit_provision_parentage.py`` produces the number the ticket's
"is this s.333, or every ``(a)`` clause?" criterion is answered with — 1,906
operative clauses detached from the provision they complete, in the dev corpus.
A number nobody can re-derive is a claim; these tests are what make it a
measurement, and they run without a database server.

The one classification that matters is the last: a clause whose tree parent is
a heading is the ABS-521 defect, and a section pathed under "Part V" whose tree
parent is the PART fragment "Part V, Chapter 19" is the ingest naming one
container two ways. Both are disagreements. Only one of them cost a reader the
60.0 m² footprint cap, so the report must not merge them.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT / "scripts") not in sys.path:
    sys.path.insert(0, str(REPO_ROOT / "scripts"))

from audit_provision_parentage import audit

from layer1.db.base import Document, SourceFragment
from layer1.db.init_db import create_all
from layer1.db.session import session_scope
from layer1.models.enums import FragmentType, ParseStatus


def _fragment(
    session,
    document_id: int,
    *,
    text: str,
    fragment_type: FragmentType,
    citation_path: str | None = None,
    parent: SourceFragment | None = None,
    reading_order: int = 1,
) -> SourceFragment:
    fragment = SourceFragment(
        document_id=document_id,
        fragment_type=fragment_type,
        citation_label=None,
        citation_path=citation_path,
        parent_fragment_id=parent.id if parent is not None else None,
        page_start=1,
        page_end=1,
        reading_order_start=reading_order,
        reading_order_end=reading_order,
        text=text,
        parse_status=ParseStatus.PARSED,
        confidence=1.0,
        source_block_ids_json=[],
        metadata_json={},
        attribute_tags=[],
    )
    session.add(fragment)
    session.flush()
    return fragment


@pytest.fixture()
def corpus(tmp_path: Path) -> str:
    """One of each population the report separates.

    * ``Part V > 333 > (a)`` — pathed under the section, parented to a heading.
      The defect.
    * ``Part V > 333 > (1.5)`` — pathed and parented to the same section. Agrees.
    * ``Part V > 333`` — pathed under "Part V", which no fragment carries.
      Unresolvable, and correctly not counted as a disagreement: nothing to
      disagree with.
    """
    db_url = f"sqlite:///{tmp_path / 'parentage.db'}"
    create_all(db_url)
    with session_scope(db_url) as session:
        document = Document(
            municipality="HRM",
            bylaw_name="Regional Centre Land Use By-Law",
            source_path="rc.txt",
            source_url=None,
            file_hash="abs521-parentage-audit",
            version_label=None,
            consolidation_date=None,
            mime_type="text/plain",
            page_count=1,
            parser_version="test",
            retrieval_enabled=True,
        )
        session.add(document)
        session.flush()

        chapter = _fragment(
            session,
            document.id,
            text="Part V, Chapter 19: Accessory Structures",
            fragment_type=FragmentType.PART,
            citation_path="Part V, Chapter 19",
            reading_order=1,
        )
        heading = _fragment(
            session,
            document.id,
            text="Accessory Structure Footprint and Area",
            fragment_type=FragmentType.HEADING,
            parent=chapter,
            reading_order=2,
        )
        section = _fragment(
            session,
            document.id,
            text="333 (1) … except:",
            fragment_type=FragmentType.SECTION,
            citation_path="Part V > 333",
            parent=chapter,
            reading_order=3,
        )
        _fragment(
            session,
            document.id,
            text="(a) … 60.0 square metres; or",
            fragment_type=FragmentType.CLAUSE,
            citation_path="Part V > 333 > (a)",
            parent=heading,
            reading_order=4,
        )
        _fragment(
            session,
            document.id,
            text="(1.5) … 93.0 square metres.",
            fragment_type=FragmentType.SUBSECTION,
            citation_path="Part V > 333 > (1.5)",
            parent=section,
            reading_order=5,
        )
    return db_url


def test_the_defect_is_counted_and_named(corpus):
    with session_scope(corpus) as session:
        report = audit(session, example_limit=10)

    assert report["disagrees"] == 1
    assert report["agrees"] == 1
    # "Part V > 333" is pathed under "Part V", which no fragment carries. That
    # is a path the ingest never materialised, not a parentage conflict.
    assert report["unresolvable_parent_path"] == 1
    assert report["operative_clauses_detached_from_their_provision"] == 1
    assert report["disagreement_shapes"] == {"clause under heading": 1}

    (example,) = report["examples"]
    assert example["citation_path"] == "Part V > 333 > (a)"
    assert example["path_says_parent_is"] == "Part V > 333"
    assert "Accessory Structure Footprint and Area" in example["tree_says_parent_is"]


def test_a_container_named_two_ways_is_not_the_abs521_population(tmp_path: Path):
    """482 of the dev corpus's 2,410 disagreements are ``section under part``.

    A section pathed ``Part V > 229`` whose tree parent is the PART fragment
    ``Part V, Chapter 9: …`` disagrees on the letter and agrees on the substance
    — the section really is in Part V. Rolling it into the headline would report
    the blast radius as 2,410 and invite the fix being judged against rows it
    was never about.
    """
    db_url = f"sqlite:///{tmp_path / 'container.db'}"
    create_all(db_url)
    with session_scope(db_url) as session:
        document = Document(
            municipality="HRM",
            bylaw_name="Regional Centre Land Use By-Law",
            source_path="rc.txt",
            source_url=None,
            file_hash="abs521-container-audit",
            version_label=None,
            consolidation_date=None,
            mime_type="text/plain",
            page_count=1,
            parser_version="test",
            retrieval_enabled=True,
        )
        session.add(document)
        session.flush()
        part = _fragment(
            session,
            document.id,
            text="Part V",
            fragment_type=FragmentType.PART,
            citation_path="Part V",
            reading_order=1,
        )
        chapter = _fragment(
            session,
            document.id,
            text="Part V, Chapter 9: Built Form",
            fragment_type=FragmentType.PART,
            citation_path="Part V, Chapter 9",
            parent=part,
            reading_order=2,
        )
        _fragment(
            session,
            document.id,
            text="229 (1) …",
            fragment_type=FragmentType.SECTION,
            citation_path="Part V > 229",
            parent=chapter,
            reading_order=3,
        )

    with session_scope(db_url) as session:
        report = audit(session, example_limit=10)

    assert report["disagrees"] == 1
    assert report["disagreement_shapes"] == {"section under part": 1}
    assert report["operative_clauses_detached_from_their_provision"] == 0


def test_documents_not_published_to_retrieval_are_out_of_scope(tmp_path: Path):
    """The audit describes what retrieval can see, and nothing else.

    A staged or superseded ingest sitting in the same database would otherwise
    inflate a number the ticket reads as "how much of the live corpus is
    affected".
    """
    db_url = f"sqlite:///{tmp_path / 'scope.db'}"
    create_all(db_url)
    with session_scope(db_url) as session:
        document = Document(
            municipality="HRM",
            bylaw_name="Staged By-law",
            source_path="staged.txt",
            source_url=None,
            file_hash="abs521-staged",
            version_label=None,
            consolidation_date=None,
            mime_type="text/plain",
            page_count=1,
            parser_version="test",
            retrieval_enabled=False,
        )
        session.add(document)
        session.flush()
        heading = _fragment(
            session,
            document.id,
            text="Some heading",
            fragment_type=FragmentType.HEADING,
            reading_order=1,
        )
        _fragment(
            session,
            document.id,
            text="333 …",
            fragment_type=FragmentType.SECTION,
            citation_path="Part V > 333",
            reading_order=2,
        )
        _fragment(
            session,
            document.id,
            text="(a) …",
            fragment_type=FragmentType.CLAUSE,
            citation_path="Part V > 333 > (a)",
            parent=heading,
            reading_order=3,
        )

    with session_scope(db_url) as session:
        report = audit(session, example_limit=10)

    assert report["fragments"] == 0
    assert report["disagrees"] == 0
    assert report["operative_clauses_detached_from_their_provision"] == 0
