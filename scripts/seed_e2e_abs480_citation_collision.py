#!/usr/bin/env python
"""Seed the citation-path-collision probe corpus (ABS-480).

Fixture for ``web/e2e/functional/abs480-citation-collision.spec.ts``.

Unlike most e2e seeds this one does not hand-write ``SourceFragment`` rows —
it runs the real ``reconstruct_hierarchy`` over five synthetic page blocks and
persists whatever the parser produces. That is the point: the behaviour under
test is what the *parser* decides about a collided provision, so a seed that
asserted the answer by writing it directly would pin nothing.

The five blocks produce:

* ``26``        section, path ``26``           — the ancestor.
* ``(g)`` x2    clauses that both derive ``26 > (g)`` — the collision. Path
                blanked, ``metadata_json.duplicate_citation_path`` recorded,
                and (ABS-480) ``parse_status`` left at ``parsed`` with the
                clause pattern's 0.85 confidence intact. Before the fix these
                came out ``uncertain`` at 0.6.
* ``(h)``       clause, path ``26 > (h)`` — the control. Same block type, same
                confidence, no collision, so any score difference between it
                and a ``(g)`` clause is the collision demotion and nothing
                else.
* a ``-`` bullet  an unlabelled list item the parser marks ``uncertain`` on its
                own merits. It must *stay* uncertain: the fix restores the
                provisions a collision demoted, it does not promote everything.

**Content parity.** Every provision contains "bicycle" exactly once, so the
query ``"bicycle"`` scores identically against all of them on content and the
only thing left to separate their scores is the ``parse_status`` bonus in
``_score_fragment`` (+1.0 parsed / -2.0 otherwise).

Holding that parity now takes one more constraint than it used to. ABS-494
blended Postgres full-text search into the text channel, and
``ts_rank_cd(..., 1)`` normalises by the fragment's **token count** — so under
FTS a shorter provision outranks a longer one on brevity alone, whatever it
says. The original four provisions ran 9 / 9 / 8 / 5 tokens, which never
mattered while the ladder was the only text ranker but became a 0.42-point
spread the moment FTS joined it: the control clause outscored its collided
sibling, and the bullet — the fragment that must stay *below* the clauses —
outscored both. Every point of that spread was length, so the spec had started
reading a brevity artefact as a collision penalty.

The four provisions are therefore equalised at nine tokens each, which makes
their FTS rank identical and hands the ordering back to ``parse_status``. The
heading is deliberately left short: no assertion compares it to a provision,
and shrinking the provisions to match it would leave them nothing to say.

The document is its own bylaw, so the spec scopes every search to it by
``bylaw_name`` and no other seeded corpus can contribute matches.

Idempotent get-or-create keyed on the document's ``file_hash``, which carries a
revision suffix — bump it whenever ``_BLOCK_TEXTS`` changes, or a database
seeded by an earlier revision will keep serving the old corpus.

Usage::

    DATABASE_URL=... python scripts/seed_e2e_abs480_citation_collision.py
"""
from __future__ import annotations

# ABS-428: must precede any advisor/layer1 import so the cached settings
# resolve DATABASE_URL to the dedicated e2e Postgres instance, never dev.
import e2e_db_default  # noqa: F401  isort: skip

import json
import sys

from sqlalchemy import select, text as sa_text

from layer1.db.base import Document, SourceFragment, utcnow
from layer1.db.session import session_scope
from layer1.models.enums import BlockType
from layer1.models.schemas import PageBlockData
from layer1.pipeline.hierarchy import reconstruct_hierarchy

# Bump the trailing revision whenever `_BLOCK_TEXTS` changes. The e2e Postgres
# is ephemeral in principle but its volume survives `make e2e` (only a
# port-mismatch recreate clears it), so an unchanged hash would let a database
# seeded by an earlier revision keep serving the old corpus forever — the seed
# would find the document, skip, and report success. `_supersede_prior_revisions`
# below is the other half: it deletes the stale revision, which otherwise shares
# this bylaw_name and would double every fragment the spec counts.
DOCUMENT_FILE_HASH = "e2e-abs480-citation-collision-2"
DOCUMENT_MUNICIPALITY = "HRM"
DOCUMENT_BYLAW_NAME = "Citation Collision Probe Bylaw (ABS-480 E2E)"

# Arbitrary but stable/unique among the e2e seeds ("abs480-collision").
ADVISORY_LOCK_KEY = 4800480

COLLIDED_LABEL = "(g)"
COLLIDED_PATH = "26 > (g)"
CONTROL_PATH = "26 > (h)"
SECTION_PATH = "26"

# Every provision block below indexes to exactly NINE tsvector token positions,
# and contains "bicycle" exactly once. That is a constraint, not a coincidence —
# see "Content parity" in the module docstring before rewording any of them. The
# heading is exempt: nothing asserts its score against a provision.
#
# Mind the off-by-one: a clause's `citation_label` is indexed *in addition to*
# the text it was parsed out of, so "(g)" occupies two of the nine positions
# ('g':1,2) and a labelled clause needs one content word fewer than the
# unlabelled bullet to tie it. Hence "equipment pad" — it is padding, and the
# extra word is doing real work.
#
# Verify a reworded line against the seeded row rather than by eye:
#   SELECT (SELECT sum(cardinality(positions))
#             FROM unnest(to_tsvector('english',
#                    coalesce(citation_label,'') || ' ' || coalesce(text,''))))
#     FROM source_fragment WHERE ...;      -- must be 9 for all four provisions
_BLOCK_TEXTS = (
    ("26 Bicycle Parking", BlockType.HEADING),
    ("(g) Every bicycle parking space shall be located on the same lot.", BlockType.LIST_ITEM),
    ("(g) Every bicycle parking space shall be sheltered from precipitation.", BlockType.LIST_ITEM),
    ("(h) Every bicycle storage locker shall be securely fastened.", BlockType.LIST_ITEM),
    (
        "- Bicycle racks shall be securely anchored to a poured concrete equipment pad.",
        BlockType.LIST_ITEM,
    ),
)


def _blocks() -> list[PageBlockData]:
    return [
        PageBlockData(
            page_number=1,
            block_type=block_type,
            reading_order=order,
            raw_text=text,
            normalized_text=text,
            parser_source="e2e-seed",
        )
        for order, (text, block_type) in enumerate(_BLOCK_TEXTS)
    ]


def _supersede_prior_revisions(session) -> int:
    """Delete any earlier revision of this probe document.

    The spec scopes its searches by ``bylaw_name``, not by ``file_hash``, so a
    superseded revision left in place would contribute a second copy of every
    fragment — four colliding ``(g)`` clauses instead of two — and fail the
    count assertions rather than the score ones. Returns the number removed.
    """
    stale = session.execute(
        select(Document).where(
            Document.bylaw_name == DOCUMENT_BYLAW_NAME,
            Document.file_hash != DOCUMENT_FILE_HASH,
        )
    ).scalars().all()
    for document in stale:
        session.execute(
            sa_text("DELETE FROM source_fragment WHERE document_id = :d").bindparams(
                d=document.id
            )
        )
        session.delete(document)
    session.flush()
    return len(stale)


def _get_or_create_document(session) -> Document:
    document = session.execute(
        select(Document).where(Document.file_hash == DOCUMENT_FILE_HASH)
    ).scalars().first()
    if document is not None:
        return document
    document = Document(
        municipality=DOCUMENT_MUNICIPALITY,
        bylaw_name=DOCUMENT_BYLAW_NAME,
        source_path="e2e/abs480_citation_collision.txt",
        file_hash=DOCUMENT_FILE_HASH,
        mime_type="text/plain",
        page_count=1,
        parser_version="e2e-seed",
        ingestion_timestamp=utcnow(),
    )
    session.add(document)
    session.flush()
    return document


def _ensure_fragments(session, document_id: int) -> int:
    existing = session.execute(
        select(SourceFragment).where(SourceFragment.document_id == document_id)
    ).scalars().first()
    if existing is not None:
        return 0
    persisted: list[SourceFragment] = []
    for data in reconstruct_hierarchy(_blocks()):
        fragment = SourceFragment(
            document_id=document_id,
            fragment_type=data.fragment_type,
            citation_label=data.citation_label,
            citation_path=data.citation_path,
            parent_fragment_id=(
                persisted[data.parent_index].id if data.parent_index is not None else None
            ),
            page_start=data.page_start,
            page_end=data.page_end,
            reading_order_start=data.reading_order_start,
            reading_order_end=data.reading_order_end,
            text=data.text,
            parse_status=data.parse_status,
            confidence=data.confidence,
            source_block_ids_json=[],
            metadata_json=data.metadata,
        )
        session.add(fragment)
        session.flush()
        persisted.append(fragment)
    return len(persisted)


def _assert_content_parity(session, document_id: int) -> int:
    """Fail the seed if the provisions no longer tie on FTS token count.

    The spec's score assertions only isolate the collision while the four
    provisions rank identically under ``ts_rank_cd``, which normalises by token
    count — see "Content parity" above. Checking it here, against the real
    ``english`` dictionary and the rows as persisted, turns a reworded line into
    an error that names the cause. Left unchecked it surfaces a minute later as
    a score comparison off by a few tenths — which looks like a scorer
    regression and is not one.

    No-op off Postgres. Returns the shared token count.
    """
    if session.bind.dialect.name != "postgresql":
        return 0
    counts = session.execute(
        sa_text(
            "SELECT id, citation_label, text, ("
            "  SELECT sum(cardinality(positions)) FROM unnest(to_tsvector('english',"
            "    coalesce(citation_label, '') || ' ' || coalesce(text, '')))"
            ") FROM source_fragment"
            " WHERE document_id = :d AND upper(fragment_type::text) <> 'SECTION'"
            " ORDER BY reading_order_start"
        ).bindparams(d=document_id)
    ).all()
    distinct = {int(row[3]) for row in counts}
    if len(distinct) > 1:
        detail = "\n".join(f"  {row[3]:>3} tokens  {row[1]!r} {row[2]!r}" for row in counts)
        raise SystemExit(
            "ABS-480 probe corpus lost content parity: the provisions must all "
            f"index to the same tsvector token count, got {sorted(distinct)}.\n"
            f"{detail}\n"
            "Pad or trim the outlier (remember a citation_label is indexed on "
            "top of the text it was parsed from) and bump DOCUMENT_FILE_HASH."
        )
    return next(iter(distinct), 0)


def main() -> int:
    with session_scope() as session:
        if session.bind.dialect.name == "postgresql":
            # ABS-207: serialise against the other Playwright viewport
            # workers, which all run this spec's beforeAll concurrently.
            session.execute(
                sa_text("SELECT pg_advisory_xact_lock(:k)").bindparams(k=ADVISORY_LOCK_KEY)
            )
        superseded = _supersede_prior_revisions(session)
        document = _get_or_create_document(session)
        created = _ensure_fragments(session, document.id)
        session.flush()
        tokens = _assert_content_parity(session, document.id)
        payload = {
            "document_id": document.id,
            "bylaw_name": DOCUMENT_BYLAW_NAME,
            "fragments_created": created,
            "revisions_superseded": superseded,
            "provision_tsvector_tokens": tokens,
            "collided_path": COLLIDED_PATH,
            "control_path": CONTROL_PATH,
        }
    print(json.dumps(payload))
    return 0


if __name__ == "__main__":
    sys.exit(main())
