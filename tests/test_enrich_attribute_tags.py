"""Coverage for the LLM-assisted attribute_tags enrichment pass.

Tests use a deterministic ``FakeTagger`` so the suite never depends on
network or API keys. The Anthropic-backed implementation is exercised
in production runs against Halifax, not in CI.

Pinned behaviours:

* Prefilter pre-filters — clauses with no keyword hits and no parent
  hit aren't sent to the LLM.
* Parent-inherited prefilter — a clause whose own text doesn't match
  but whose parent does still gets sent.
* Hedge filter — proposals containing hedge words ("may", "could")
  are discarded into ``attribute_tag_discards``.
* Invalid attribute ids are discarded.
* Audit trail is written: ``attribute_tag_audit`` carries taxonomy
  version + model + timestamp, ``attribute_tag_rationales`` carries
  the accepted proposals.
* Idempotency — second run with the same model + taxonomy + result
  is a no-op (``unchanged_skipped`` increments).
* Re-run with a *different* model writes a new audit row and
  preserves the old one in ``attribute_tag_history``.
* Dry-run doesn't touch the DB.
"""
from __future__ import annotations

from pathlib import Path
from typing import Iterable

from layer1.db.base import Document, SourceFragment
from layer1.db.init_db import create_all
from layer1.db.session import session_scope
from layer1.models.enums import FragmentType, ParseStatus
from layer2.compliance.taxonomy import Taxonomy, load_taxonomy
from layer2.semantic.enrich_attribute_tags import (
    PROMPT_VERSION,
    EnrichStats,
    LLMTagger,
    TagProposal,
    enrich_document,
)


class FakeTagger:
    """Deterministic tagger driven by a hard-coded clause→proposals map.

    The map is keyed on a stable substring of the clause text so
    tests can be written without juggling fragment ids.
    """

    name = "fake-tagger"

    def __init__(
        self,
        responses: dict[str, list[TagProposal]],
        *,
        model: str = "fake-model-1",
        raise_on: Iterable[str] = (),
    ) -> None:
        self._responses = responses
        self._raise_on = tuple(raise_on)
        self.model = model
        self.calls: list[tuple[str, list[str]]] = []

    def propose_tags(
        self,
        *,
        clause_text: str,
        citation_context: str,
        taxonomy: Taxonomy,
        candidate_ids: list[str],
    ) -> list[TagProposal]:
        self.calls.append((clause_text, candidate_ids))
        for fragment_text, proposals in self._responses.items():
            if fragment_text in clause_text:
                for raise_keyword in self._raise_on:
                    if raise_keyword in clause_text:
                        raise RuntimeError(f"fake LLM error for {raise_keyword!r}")
                return proposals
        return []


def _make_db(tmp_path: Path) -> str:
    db_url = f"sqlite:///{tmp_path / 'enrich.db'}"
    create_all(db_url)
    return db_url


def _seed_document(session) -> tuple[int, dict[str, int]]:
    document = Document(
        municipality="HRM",
        bylaw_name="Test Bylaw",
        source_path="t.pdf",
        file_hash="h",
        mime_type="application/pdf",
        page_count=1,
        parser_version="test",
    )
    session.add(document)
    session.flush()

    setbacks_heading = SourceFragment(
        document_id=document.id,
        fragment_type=FragmentType.SECTION,
        citation_label="4",
        citation_path="4",
        parent_fragment_id=None,
        page_start=1,
        page_end=1,
        reading_order_start=1,
        # Parent text mentions "front yard" so the prefilter on the
        # parent inherits to children that don't carry the keyword
        # in their own text (e.g. just "the dimension specified
        # shall be X metres").
        text="4. Front yard requirements. Every dwelling must satisfy the rules below.",
        parse_status=ParseStatus.PARSED,
        confidence=1.0,
    )
    session.add(setbacks_heading)
    session.flush()

    front_clause = SourceFragment(
        document_id=document.id,
        fragment_type=FragmentType.CLAUSE,
        citation_label="4.1",
        citation_path="4.1",
        parent_fragment_id=setbacks_heading.id,
        page_start=1,
        page_end=1,
        reading_order_start=2,
        text="The minimum front yard shall be 4.5 metres.",
        parse_status=ParseStatus.PARSED,
        confidence=1.0,
    )
    bare_clause = SourceFragment(
        document_id=document.id,
        fragment_type=FragmentType.CLAUSE,
        citation_label="4.2",
        citation_path="4.2",
        parent_fragment_id=setbacks_heading.id,
        page_start=1,
        page_end=1,
        reading_order_start=3,
        text="The dimension specified shall be 6.0 metres.",
        parse_status=ParseStatus.PARSED,
        confidence=1.0,
    )
    definitions = SourceFragment(
        document_id=document.id,
        fragment_type=FragmentType.SECTION,
        citation_label="5",
        citation_path="5",
        parent_fragment_id=None,
        page_start=2,
        page_end=2,
        reading_order_start=1,
        text="Definitions of building, lot, and yard apply across this bylaw.",
        parse_status=ParseStatus.PARSED,
        confidence=1.0,
    )
    unrelated_clause = SourceFragment(
        document_id=document.id,
        fragment_type=FragmentType.CLAUSE,
        citation_label="6.1",
        citation_path="6.1",
        parent_fragment_id=None,
        page_start=3,
        page_end=3,
        reading_order_start=1,
        text="The mayor shall publish notices in the city gazette.",
        parse_status=ParseStatus.PARSED,
        confidence=1.0,
    )
    session.add_all([front_clause, bare_clause, definitions, unrelated_clause])
    session.flush()
    return document.id, {
        "heading": setbacks_heading.id,
        "front": front_clause.id,
        "bare": bare_clause.id,
        "definitions": definitions.id,
        "unrelated": unrelated_clause.id,
    }


def test_prefilter_skips_clauses_with_no_keyword_hit(tmp_path: Path) -> None:
    db_url = _make_db(tmp_path)
    with session_scope(db_url) as session:
        document_id, _ids = _seed_document(session)

    tagger = FakeTagger({})
    with session_scope(db_url) as session:
        stats = enrich_document(session, document_id=document_id, tagger=tagger)

    # The unrelated 6.1 clause never touches an attribute keyword and
    # its parent is None — must not reach the LLM.
    sent_texts = {clause_text for clause_text, _candidates in tagger.calls}
    assert all("mayor" not in text for text in sent_texts)
    assert stats.fragments_prefiltered_out >= 1


def test_parent_keyword_inherits_to_children(tmp_path: Path) -> None:
    db_url = _make_db(tmp_path)
    with session_scope(db_url) as session:
        document_id, _ids = _seed_document(session)

    tagger = FakeTagger(
        {
            "Front yard requirements.": [],
            "minimum front yard": [
                TagProposal(
                    attribute_id="front_setback_m",
                    rationale="Clause sets the minimum front yard distance.",
                )
            ],
            "dimension specified shall be 6.0": [
                TagProposal(
                    attribute_id="front_setback_m",
                    rationale="Inherits from the Front yard requirements heading.",
                )
            ],
        }
    )
    with session_scope(db_url) as session:
        enrich_document(session, document_id=document_id, tagger=tagger)

    # The 4.2 clause's own text mentions "metres" and "dimension"
    # but not "setback"/"yard"; its parent (4. Setbacks) does, so
    # it should still be sent.
    sent_texts = {clause_text for clause_text, _candidates in tagger.calls}
    assert any("dimension specified" in t for t in sent_texts)

    with session_scope(db_url) as session:
        fragment = (
            session.query(SourceFragment)
            .filter(SourceFragment.citation_path == "4.2")
            .one()
        )
        assert "front_setback_m" in fragment.attribute_tags


def test_hedged_proposals_are_discarded(tmp_path: Path) -> None:
    db_url = _make_db(tmp_path)
    with session_scope(db_url) as session:
        document_id, ids = _seed_document(session)

    tagger = FakeTagger(
        {
            "Front yard requirements.": [],
            "minimum front yard": [
                TagProposal(
                    attribute_id="front_setback_m",
                    rationale="Clause sets the minimum front yard distance.",
                ),
                TagProposal(
                    attribute_id="lot_coverage_percent",
                    rationale="May indirectly affect lot coverage on small lots.",
                ),
            ],
        }
    )
    with session_scope(db_url) as session:
        stats = enrich_document(session, document_id=document_id, tagger=tagger)
    assert stats.hedge_discards == 1

    with session_scope(db_url) as session:
        fragment = session.get(SourceFragment, ids["front"])
        assert "front_setback_m" in fragment.attribute_tags
        # lot_coverage_percent must NOT be tagged because the
        # rationale contained "may".
        assert "lot_coverage_percent" not in fragment.attribute_tags
        # Discards land in the audit metadata for spot-check.
        discards = fragment.metadata_json.get("attribute_tag_discards") or []
        assert any(d["attribute_id"] == "lot_coverage_percent" for d in discards)


def test_invalid_attribute_ids_are_discarded(tmp_path: Path) -> None:
    db_url = _make_db(tmp_path)
    with session_scope(db_url) as session:
        document_id, ids = _seed_document(session)

    tagger = FakeTagger(
        {
            "minimum front yard": [
                TagProposal(
                    attribute_id="front_setback_m",
                    rationale="Sets the minimum front yard distance.",
                ),
                TagProposal(
                    attribute_id="totally_invented_attribute",
                    rationale="Hallucinated id; must be dropped.",
                ),
            ],
        }
    )
    with session_scope(db_url) as session:
        enrich_document(session, document_id=document_id, tagger=tagger)

    with session_scope(db_url) as session:
        fragment = session.get(SourceFragment, ids["front"])
        assert fragment.attribute_tags == ["front_setback_m"]


def test_audit_trail_is_written(tmp_path: Path) -> None:
    db_url = _make_db(tmp_path)
    with session_scope(db_url) as session:
        document_id, ids = _seed_document(session)

    tagger = FakeTagger(
        {
            "minimum front yard": [
                TagProposal(
                    attribute_id="front_setback_m",
                    rationale="Sets the front yard minimum.",
                )
            ]
        }
    )
    with session_scope(db_url) as session:
        enrich_document(session, document_id=document_id, tagger=tagger)

    taxonomy = load_taxonomy()
    with session_scope(db_url) as session:
        fragment = session.get(SourceFragment, ids["front"])
        audit = fragment.metadata_json["attribute_tag_audit"]
        assert audit["taxonomy_version"] == taxonomy.version
        assert audit["model"] == "fake-model-1"
        assert audit["prompt_version"] == PROMPT_VERSION
        assert audit["tag_count"] == 1
        rationales = fragment.metadata_json["attribute_tag_rationales"]
        assert rationales == [
            {
                "attribute_id": "front_setback_m",
                "rationale": "Sets the front yard minimum.",
            }
        ]


def test_idempotent_rerun_is_noop(tmp_path: Path) -> None:
    db_url = _make_db(tmp_path)
    with session_scope(db_url) as session:
        document_id, _ids = _seed_document(session)

    responses = {
        "minimum front yard": [
            TagProposal(
                attribute_id="front_setback_m",
                rationale="Sets the front yard minimum.",
            )
        ]
    }
    with session_scope(db_url) as session:
        first = enrich_document(
            session, document_id=document_id, tagger=FakeTagger(responses)
        )

    with session_scope(db_url) as session:
        second = enrich_document(
            session, document_id=document_id, tagger=FakeTagger(responses)
        )
    assert second.tags_assigned == 0
    assert second.unchanged_skipped >= first.fragments_with_tags


def test_rerun_with_different_model_preserves_history(tmp_path: Path) -> None:
    db_url = _make_db(tmp_path)
    with session_scope(db_url) as session:
        document_id, ids = _seed_document(session)

    with session_scope(db_url) as session:
        enrich_document(
            session,
            document_id=document_id,
            tagger=FakeTagger(
                {
                    "minimum front yard": [
                        TagProposal(
                            attribute_id="front_setback_m",
                            rationale="model-1 rationale",
                        )
                    ]
                },
                model="fake-model-1",
            ),
        )

    with session_scope(db_url) as session:
        enrich_document(
            session,
            document_id=document_id,
            tagger=FakeTagger(
                {
                    "minimum front yard": [
                        TagProposal(
                            attribute_id="front_setback_m",
                            rationale="model-2 rationale",
                        )
                    ]
                },
                model="fake-model-2",
            ),
        )

    with session_scope(db_url) as session:
        fragment = session.get(SourceFragment, ids["front"])
        history = fragment.metadata_json["attribute_tag_history"]
        assert len(history) == 1
        assert history[0]["model"] == "fake-model-1"
        current = fragment.metadata_json["attribute_tag_audit"]
        assert current["model"] == "fake-model-2"


def test_dry_run_does_not_persist(tmp_path: Path) -> None:
    db_url = _make_db(tmp_path)
    with session_scope(db_url) as session:
        document_id, ids = _seed_document(session)

    tagger = FakeTagger(
        {
            "minimum front yard": [
                TagProposal(
                    attribute_id="front_setback_m",
                    rationale="Sets the minimum front yard distance.",
                )
            ]
        }
    )
    with session_scope(db_url) as session:
        stats = enrich_document(
            session, document_id=document_id, tagger=tagger, dry_run=True
        )
    assert stats.fragments_with_tags >= 1

    with session_scope(db_url) as session:
        fragment = session.get(SourceFragment, ids["front"])
        assert fragment.attribute_tags == []
        assert "attribute_tag_audit" not in (fragment.metadata_json or {})


def test_commit_every_flushes_partial_progress(tmp_path: Path) -> None:
    """A mid-run failure preserves chunks already committed.

    Why this matters: production runs against Halifax issue ~1500
    sequential LLM calls over ~90 minutes inside one session. Without
    chunked commits, a connection blip late in the run discards all
    of the paid LLM work. With ``commit_every=N`` set, only the
    in-flight chunk is lost.
    """
    db_url = _make_db(tmp_path)
    with session_scope(db_url) as session:
        document = Document(
            municipality="HRM",
            bylaw_name="Test Bylaw",
            source_path="t.pdf",
            file_hash="h",
            mime_type="application/pdf",
            page_count=1,
            parser_version="test",
        )
        session.add(document)
        session.flush()
        document_id = document.id
        heading = SourceFragment(
            document_id=document_id,
            fragment_type=FragmentType.SECTION,
            citation_label="4",
            citation_path="4",
            parent_fragment_id=None,
            page_start=1,
            page_end=1,
            reading_order_start=0,
            text="4. Front yard requirements.",
            parse_status=ParseStatus.PARSED,
            confidence=1.0,
        )
        session.add(heading)
        session.flush()
        clause_ids: list[int] = []
        for i in range(5):
            clause = SourceFragment(
                document_id=document_id,
                fragment_type=FragmentType.CLAUSE,
                citation_label=f"4.{i + 1}",
                citation_path=f"4.{i + 1}",
                parent_fragment_id=heading.id,
                page_start=1,
                page_end=1,
                reading_order_start=i + 1,
                text=f"Clause {i + 1}: the minimum front yard shall be {i + 4} metres.",
                parse_status=ParseStatus.PARSED,
                confidence=1.0,
            )
            session.add(clause)
            session.flush()
            clause_ids.append(clause.id)

    tagger = FakeTagger(
        {
            "Front yard requirements.": [],
            "Clause 1": [TagProposal(attribute_id="front_setback_m", rationale="front yard 4 m")],
            "Clause 2": [TagProposal(attribute_id="front_setback_m", rationale="front yard 5 m")],
            "Clause 3": [TagProposal(attribute_id="front_setback_m", rationale="front yard 6 m")],
            "Clause 4": [TagProposal(attribute_id="front_setback_m", rationale="front yard 7 m")],
            "Clause 5": [TagProposal(attribute_id="front_setback_m", rationale="front yard 8 m")],
        },
    )

    # Simulate a hard mid-run blip: patch _persist_enrichment to raise
    # on the persist call for c4 (5th call: heading + c1 + c2 + c3 +
    # c4). With commit_every=2, commits land after persist #2 (heading
    # + c1) and persist #4 (c2 + c3). The exception on c4 propagates
    # out of enrich_document and session_scope rolls back; c4's persist
    # call never modified session state because the raise is before
    # original_persist runs.
    from layer2.semantic import enrich_attribute_tags as mod

    original_persist = mod._persist_enrichment
    persist_calls = {"n": 0}

    def raising_persist(**kwargs):
        persist_calls["n"] += 1
        if persist_calls["n"] == 5:
            raise RuntimeError("simulated connection blip during c4 persist")
        return original_persist(**kwargs)

    mod._persist_enrichment = raising_persist
    try:
        try:
            with session_scope(db_url) as session:
                enrich_document(
                    session,
                    document_id=document_id,
                    tagger=tagger,
                    commit_every=2,
                )
        except RuntimeError:
            pass
    finally:
        mod._persist_enrichment = original_persist

    # Two chunks committed (heading+c1, c2+c3) so c1..c3 survive.
    # c4's persist call raised before touching session state, and c5
    # was never reached.
    with session_scope(db_url) as session:
        for clause_id in clause_ids[:3]:
            fragment = session.get(SourceFragment, clause_id)
            assert fragment.attribute_tags == ["front_setback_m"], (
                f"clause {fragment.citation_path} lost its tag after chunk commit"
            )
        for clause_id in clause_ids[3:]:
            fragment = session.get(SourceFragment, clause_id)
            assert fragment.attribute_tags == [], (
                f"clause {fragment.citation_path} was unexpectedly tagged "
                "despite the simulated mid-run blip"
            )


def test_commit_every_is_ignored_under_dry_run(tmp_path: Path) -> None:
    """``commit_every`` must not bypass the dry-run guarantee."""
    db_url = _make_db(tmp_path)
    with session_scope(db_url) as session:
        document_id, ids = _seed_document(session)

    tagger = FakeTagger(
        {
            "minimum front yard": [
                TagProposal(
                    attribute_id="front_setback_m",
                    rationale="Sets the minimum front yard distance.",
                )
            ]
        }
    )
    with session_scope(db_url) as session:
        enrich_document(
            session,
            document_id=document_id,
            tagger=tagger,
            dry_run=True,
            commit_every=1,
        )

    with session_scope(db_url) as session:
        fragment = session.get(SourceFragment, ids["front"])
        assert fragment.attribute_tags == []
        assert "attribute_tag_audit" not in (fragment.metadata_json or {})


def test_llm_failure_does_not_abort_run(tmp_path: Path) -> None:
    db_url = _make_db(tmp_path)
    with session_scope(db_url) as session:
        document_id, ids = _seed_document(session)

    tagger = FakeTagger(
        {
            "minimum front yard": [
                TagProposal(
                    attribute_id="front_setback_m",
                    rationale="Sets the minimum front yard distance.",
                )
            ],
            "dimension specified shall be 6.0": [],
        },
        raise_on=("dimension specified",),
    )
    with session_scope(db_url) as session:
        stats = enrich_document(session, document_id=document_id, tagger=tagger)
    assert stats.llm_errors == 1
    # Front-yard clause still got tagged despite the unrelated failure.
    with session_scope(db_url) as session:
        fragment = session.get(SourceFragment, ids["front"])
        assert "front_setback_m" in fragment.attribute_tags
