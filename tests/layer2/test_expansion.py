from layer1.db.session import session_scope
from layer2.models.schemas import CandidateFragment
from layer2.retrieval.expansion import expand_cross_references, expand_hierarchy


def test_expand_hierarchy_adds_parent_and_sibling(prepared_document):
    with session_scope(prepared_document["db_url"]) as session:
        candidates = [
            CandidateFragment(
                source_fragment_id=7,
                source_type="fragment",
                retrieval_channel="full_text",
                base_score=0.8,
                text="(i) A temporary use may be permitted under subsection 1.2.",
            )
        ]
        expanded = expand_hierarchy(session, prepared_document["document_id"], candidates)
        fragment_ids = {candidate.source_fragment_id for candidate in expanded}
        assert 4 in fragment_ids
        assert 6 in fragment_ids


def test_expand_cross_references_adds_resolved_target(prepared_document):
    with session_scope(prepared_document["db_url"]) as session:
        candidates = [
            CandidateFragment(
                source_fragment_id=11,
                source_type="fragment",
                retrieval_channel="full_text",
                base_score=0.8,
                text="Residential zones are listed in Schedule B.",
            )
        ]
        expanded = expand_cross_references(session, candidates)
        fragment_ids = {candidate.source_fragment_id for candidate in expanded}
        assert 12 in fragment_ids
