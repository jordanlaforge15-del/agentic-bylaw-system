"""Zone-scope binding: which provisions a zone-scoped query is *about* (ABS-500).

A dimensional question is almost always zone-scoped — "what is the maximum
required lot coverage in the ER-1 zone" — and in a land-use by-law the clause
that answers it does **not** name the zone:

    Part V, Chapter 9: Built Form and Siting Requirements within the ER3,
                       ER-2, and ER-1 Zones
      229  … the minimum required side setback for any main building shall be:
      231  … the maximum required lot coverage shall be:

The zone is declared once, by the chapter, and every section beneath it
inherits it. Meanwhile dozens of unrelated clauses elsewhere in the by-law
*mention* ER-1 in passing ("where a lot abuts another lot, any portion of
which, is zoned HR-2, HR-1, ER-3, ER-2, ER-1, …"). Before this module the
scorer paid +4 for the passing mention (own text) and +2 for the governing
chapter (inherited context), so a landscaping clause that merely lists ER-1
among abutting zones outranked the section that states the ER-1 standard.
That inversion, not the absence of a table channel, is what held the
``dimensional`` class at Recall@10 = 0.056 on the ABS-486 set.

The rule this module supplies is one sentence: **a clause governed by a
container that declares the query's zone states that zone as surely as if it
carried it in its own citation path**, and is scored at the citation-path rung
(+12) rather than the inherited-context rung (+2). It is a *structural* claim,
which is why the credit matches the structural weight — the chapter heading is
part of how the clause is addressed, not prose that happens to repeat a term.

Two guards keep it from flooding the ranking:

* The declaring container is bound to its own zone as well, so a question
  asking *for the chapter* ("where are the built form requirements for the COR
  zone") still ranks the heading above the sections it scopes.
* The vocabulary is the corpus's own extracted zone entities, not a regex over
  anything hyphen-shaped, so "Section 3-1" and "Table 1A" cannot bind.

The same vocabulary drives the table channel's axis binding
(:mod:`bylaw_retrieval.retrieval.tables`): a zone-scoped query binds to a
*column* of a dimensional matrix by exactly the same identity it binds to a
*chapter* of prose.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from sqlalchemy import select

from layer1.db.base import SemanticEntity, SourceFragment
from layer1.models.enums import FragmentType

#: Fragment types whose text is a container heading rather than a provision.
#: A zone code in one of these scopes everything beneath it; a zone code in a
#: SECTION or CLAUSE is the clause talking about a zone, which is already
#: scored as own text. SCHEDULE/APPENDIX/HEADING are included because not every
#: ingest produces PART containers — the Halifax Mainland by-law has none — and
#: a rule that only fires on one by-law's tree shape is not a retrieval rule.
_CONTAINER_TYPES = (
    FragmentType.PART,
    FragmentType.SCHEDULE,
    FragmentType.APPENDIX,
    FragmentType.HEADING,
)

#: A zone code is written with or without its hyphen across a single corpus —
#: the Regional Centre's own Chapter 9 heading says "ER3, ER-2, and ER-1" in
#: one breath. Matching hyphen-optionally is therefore not leniency, it is
#: reading the document as printed.
_HYPHEN = re.compile(r"-")

#: Zone codes short enough to collide with ordinary words or letters once the
#: hyphen is optional ("H", "A", "DD"). A one-character code would bind every
#: clause containing a stray initial, so the vocabulary drops them: a query
#: naming such a zone falls back to plain keyword scoring rather than binding
#: the wrong half of the by-law.
_MIN_ZONE_CODE_LENGTH = 2


def _zone_pattern(code: str) -> re.Pattern[str]:
    """Word-bounded, hyphen-optional matcher for one zone code.

    Hand-rolled boundaries (``[A-Za-z0-9]`` lookarounds) rather than ``\\b`` so
    ``HR-2`` does not match inside ``HR-21`` — a trailing digit satisfies
    ``\\b`` after the ``2`` and would bind the wrong zone.
    """
    body = _HYPHEN.sub("-?", re.escape(code).replace("\\-", "-"))
    return re.compile(rf"(?<![A-Za-z0-9]){body}(?![A-Za-z0-9])", re.IGNORECASE)


@dataclass(frozen=True)
class ZoneScopeIndex:
    """The zone vocabulary of a corpus, plus the containers that declare each.

    Built once per document scope and cached on the service: the vocabulary is
    a property of the ingest, not of the request, and rebuilding it per query
    would put two extra table scans in front of every search.
    """

    #: canonical zone code -> matcher. Ordered longest-code-first so a query
    #: naming "ER-1" is not also credited to a hypothetical "ER".
    patterns: tuple[tuple[str, re.Pattern[str]], ...]
    #: container fragment id -> the zone codes its heading declares.
    declared_by_container: dict[int, frozenset[str]]

    def zones_named_in(self, text: str) -> frozenset[str]:
        """The zone codes ``text`` names as whole words."""
        if not text:
            return frozenset()
        return frozenset(code for code, pattern in self.patterns if pattern.search(text))

    def containers_declaring(self, zones: frozenset[str]) -> frozenset[int]:
        """Container fragment ids whose heading declares any of ``zones``."""
        if not zones:
            return frozenset()
        return frozenset(
            container_id
            for container_id, declared in self.declared_by_container.items()
            if declared & zones
        )

    def containers_excluding(self, zones: frozenset[str]) -> frozenset[int]:
        """Container fragment ids that declare zones, but none of ``zones``.

        The complement of :meth:`containers_declaring` over the *declaring*
        containers only — a container that names no zone at all (``Part V,
        Chapter 1: General Built Form and Siting Requirements``) is silent on
        the question, not adverse to it, and is deliberately absent from both
        sets.

        This is the half of zone-scope binding ABS-500 left out and ABS-518
        needed. Crediting the right chapter is not enough when the by-law
        states the *same rule shape* over different numbers in a dozen
        chapters: "Table 9: Minimum required side setbacks …" sits under
        ``Part V, Chapter 9 … within the ER3, ER-2, and ER-1 Zones`` and its
        caption matches an HR-1 side-setback question word for word, so on
        caption tokens alone it outranks ``s.198``, which states the HR-1
        standard and never spells out its own zone. A chapter heading that
        names ER and not HR is a positive statement that its sections do not
        govern HR-1, and the ranking has to read it as one.
        """
        if not zones:
            return frozenset()
        return frozenset(
            container_id
            for container_id, declared in self.declared_by_container.items()
            if declared and not (declared & zones)
        )


def build_zone_scope_index(session, document_ids) -> ZoneScopeIndex:
    """Read the zone vocabulary and the containers that declare each zone.

    ``document_ids`` is the *default* document scope, not the request's — a
    request that narrows to one document still needs the same vocabulary, and
    keying the cache on the narrowed scope would rebuild it per filter shape
    for no gain.
    """
    scope = list(document_ids) if document_ids is not None else None

    entity_stmt = select(SemanticEntity.canonical_name).where(
        SemanticEntity.entity_type == "zone"
    )
    if scope is not None:
        entity_stmt = entity_stmt.where(SemanticEntity.document_id.in_(scope))
    codes = {
        code.strip()
        for code in session.execute(entity_stmt).scalars().all()
        if code and len(code.strip()) >= _MIN_ZONE_CODE_LENGTH
    }
    patterns = tuple(
        (code, _zone_pattern(code)) for code in sorted(codes, key=lambda c: (-len(c), c))
    )
    if not patterns:
        return ZoneScopeIndex(patterns=(), declared_by_container={})

    container_stmt = select(SourceFragment.id, SourceFragment.text).where(
        SourceFragment.fragment_type.in_(_CONTAINER_TYPES)
    )
    if scope is not None:
        container_stmt = container_stmt.where(SourceFragment.document_id.in_(scope))

    declared: dict[int, frozenset[str]] = {}
    for fragment_id, text in session.execute(container_stmt).all():
        if not text:
            continue
        hits = frozenset(code for code, pattern in patterns if pattern.search(text))
        if hits:
            declared[fragment_id] = hits
    return ZoneScopeIndex(patterns=patterns, declared_by_container=declared)
