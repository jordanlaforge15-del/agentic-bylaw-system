"""Provision completion: a clause travels with the provision it completes (ABS-521).

s.333(1) of the Regional Centre reads, in full::

    333 (1) Any new accessory structure shall have no restriction on the
            maximum size of its footprint, except:

That is the whole fragment. The sentence stops on a colon because the standard
it announces is stated one level down::

    (a) subject to Clause 333(1)(b), in any DD, DH, CEN-2, CEN1, COR, HR-2,
        HR-1, ER-3, ER-2, ER-1, CH-2, or CH-1 zone: 60.0 square metres; or
    (b) in the Westmount Subdivision (WS) Special Area … 6.0 square metres
        within a front yard.

and its sibling subsection ``(1.5)`` states a second, independent cap — 93.0
square metres of *floor area* — that binds alongside the footprint cap rather
than instead of it. An owner told only the 93.0 figure can design a structure
that satisfies it and still fails s.333(1)(a).

Retrieval reached ``Part V > 333`` and ``Part V > 333 > (1.5)`` and never
``(a)``. Two facts make that unfixable by ranking, and both are why this module
completes the provision structurally instead:

1. **The clause has no topic words of its own.** Every term a question would
   use — "accessory", "structure", "footprint", "maximum" — lives in the stem.
   ``(a)`` is a zone list and a number. Scored on its own text it is worth 9
   points against the section's 21 on the very query it answers, and no weight
   on any channel closes that gap without promoting every other bare list item
   in the corpus at the same time.

2. **Its tree parent is not its section.** ``(a)`` and ``(b)`` carry
   ``citation_path`` "Part V > 333 > (a)" but ``parent_fragment_id`` pointing at
   fragment 7874, the *heading* "Accessory Structure Footprint and Area" that
   precedes s.333 — a sibling of the section, not the section. So the ancestor
   walk in :mod:`bylaw_retrieval.retrieval.context` never reaches the stem, and
   the clause inherits scope from a heading instead of from the sentence it
   finishes. This is not one bad row: **1,906 CLAUSE/SUBCLAUSE fragments in the
   dev corpus** have a ``citation_path`` naming a section as their parent and a
   ``parent_fragment_id`` pointing at a HEADING. The blast radius question the
   ticket asks — "is this s.333, or every ``(a)`` clause?" — answers *every*.

The path is the citable truth (ABS-488 repathed clauses onto the container that
scopes them); the tree link is where the parser happened to hang them. So every
lineage question this module answers is asked of the **citation path**, with the
tree used only as a second, additive source of context.

What the module does not do
---------------------------
It does not re-parent anything and it does not touch a score. Ranking is
unchanged by design: the fix is that whatever the ranker returns arrives
*complete*, which is a payload guarantee, not an ordering one. The regression
guard for that distinction is that ``evals/retrieval/BASELINE.json`` moves only
by its fingerprint when this lands, never by a metric.
"""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from layer1.db.base import SourceFragment
from layer1.models.enums import FragmentType

_PATH_SEPARATOR = ">"

#: Fragment types that are a *citable provision* — something a reader would
#: quote a standard from, and therefore something whose operative clauses are
#: part of its meaning rather than merely nearby.
PROVISION_TYPES: frozenset[FragmentType] = frozenset(
    {
        FragmentType.SECTION,
        FragmentType.SUBSECTION,
        FragmentType.CLAUSE,
        FragmentType.SUBCLAUSE,
    }
)

#: Fragment types that *contain* provisions rather than state one. Expanding
#: these is what would turn "complete the provision" into "dump the by-law":
#: one Part in the dev corpus has 297 direct path-children.
CONTAINER_TYPES: frozenset[FragmentType] = frozenset(
    {
        FragmentType.PART,
        FragmentType.SCHEDULE,
        FragmentType.APPENDIX,
        FragmentType.HEADING,
    }
)

#: Most clauses a caller could want, before the response starts describing the
#: by-law rather than answering the question. 96% of the dev corpus's 284
#: sections with clauses have 12 or fewer; the largest has 60. When the cap
#: bites, the count of what was dropped is reported — a silently truncated
#: provision reads as a complete one, which is the defect this module exists to
#: remove, not a cost worth paying to enforce a limit.
OPERATIVE_CLAUSE_LIMIT = 12

#: Guard against a malformed path (or a pathological depth) turning the ascent
#: into an unbounded walk. The deepest citation path in the dev corpus is 5
#: segments.
_MAX_PATH_DEPTH = 32


def normalise_citation_path(citation_path: str | None) -> str | None:
    """Canonicalise separator spacing so two spellings of one path compare equal.

    The ingest writes ``"Part V > 333 > (a)"``; nothing guarantees a future one
    writes the same whitespace, and a lineage that silently stopped resolving
    because of a space would look exactly like a provision with no clauses.
    """
    if not citation_path:
        return None
    segments = [
        segment.strip()
        for segment in citation_path.split(_PATH_SEPARATOR)
        if segment.strip()
    ]
    if not segments:
        return None
    return f" {_PATH_SEPARATOR} ".join(segments)


def parent_citation_path(citation_path: str | None) -> str | None:
    """``"Part V > 333 > (a)"`` -> ``"Part V > 333"``; a root path -> ``None``."""
    normalised = normalise_citation_path(citation_path)
    if normalised is None:
        return None
    segments = normalised.split(f" {_PATH_SEPARATOR} ")
    if len(segments) < 2:
        return None
    return f" {_PATH_SEPARATOR} ".join(segments[:-1])


def path_depth(citation_path: str | None) -> int:
    """Number of segments in a path; 0 when it has none."""
    normalised = normalise_citation_path(citation_path)
    if normalised is None:
        return 0
    return len(normalised.split(f" {_PATH_SEPARATOR} "))


class ProvisionLineage:
    """Path-addressed lineage for one document scope, memoised per service.

    Every lookup is one indexed query against ``uq_fragment_citation_path``
    (``document_id`` leading), and both directions are cached: a search returns
    five matches that frequently share a section, and a section's clause list is
    asked for once per match that names it.

    The cache is per-instance and per-request-lifetime rather than global. A
    ``RetrievalService`` is constructed around one session, and holding
    fragments from a closed session would hand a caller detached ORM objects —
    so only *ids* are cached, and the fragments are re-fetched from the live
    session by primary key.
    """

    def __init__(self, session: Session) -> None:
        self._session = session
        #: (document_id, normalised_path) -> fragment id, or None when the path
        #: names nothing. The negative entry matters: "Part V" is a path prefix
        #: that no fragment carries, and re-querying it for every clause in the
        #: Part would be the N+1 this cache exists to avoid.
        self._by_path: dict[tuple[int, str], int | None] = {}
        #: (document_id, normalised_path) -> direct path-children ids, in
        #: reading order.
        self._children: dict[tuple[int, str], tuple[int, ...]] = {}

    # ------------------------------------------------------------------
    # Path resolution
    # ------------------------------------------------------------------

    def fragment_at_path(
        self, document_id: int, citation_path: str | None
    ) -> SourceFragment | None:
        normalised = normalise_citation_path(citation_path)
        if normalised is None:
            return None
        key = (document_id, normalised)
        if key not in self._by_path:
            fragment = (
                self._session.execute(
                    select(SourceFragment).where(
                        SourceFragment.document_id == document_id,
                        SourceFragment.citation_path == normalised,
                    )
                )
                .scalars()
                .first()
            )
            self._by_path[key] = fragment.id if fragment is not None else None
            if fragment is not None:
                return fragment
            return None
        cached = self._by_path[key]
        if cached is None:
            return None
        return self._session.get(SourceFragment, cached)

    def path_parent(self, fragment: SourceFragment) -> SourceFragment | None:
        """The fragment the citation path says contains this one, if it exists.

        Distinct from ``fragment.parent`` and, for 1,906 clauses in the dev
        corpus, a *different fragment*. See the module docstring.
        """
        return self.fragment_at_path(
            fragment.document_id, parent_citation_path(fragment.citation_path)
        )

    def direct_path_children(
        self, document_id: int, citation_path: str | None
    ) -> list[SourceFragment]:
        """Fragments exactly one path segment below ``citation_path``.

        Filtered to *direct* children in Python rather than with a second
        ``NOT LIKE '… > % > %'`` predicate: the prefix scan is already narrow
        (a section's subtree), and a path segment is free to contain the
        wildcard characters ``%`` and ``_`` — the bracketed container prose
        ABS-488 puts into a path is arbitrary by-law English.
        """
        normalised = normalise_citation_path(citation_path)
        if normalised is None:
            return []
        key = (document_id, normalised)
        if key not in self._children:
            prefix = f"{normalised} {_PATH_SEPARATOR} "
            escaped = (
                prefix.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
            )
            candidates = (
                self._session.execute(
                    select(SourceFragment)
                    .where(
                        SourceFragment.document_id == document_id,
                        SourceFragment.citation_path.like(f"{escaped}%", escape="\\"),
                    )
                    .order_by(
                        SourceFragment.reading_order_start,
                        SourceFragment.id,
                    )
                )
                .scalars()
                .all()
            )
            depth = path_depth(normalised)
            self._children[key] = tuple(
                candidate.id
                for candidate in candidates
                if path_depth(candidate.citation_path) == depth + 1
                and normalise_citation_path(candidate.citation_path) != normalised
            )
        return [
            child
            for child in (
                self._session.get(SourceFragment, child_id)
                for child_id in self._children[key]
            )
            if child is not None
        ]

    # ------------------------------------------------------------------
    # The two questions the retriever asks
    # ------------------------------------------------------------------

    def governing_provision(self, fragment: SourceFragment) -> SourceFragment | None:
        """The provision whose operative clauses complete ``fragment``.

        Two arms, and they are the same rule read from either end:

        * ``fragment`` **is** the provision when it is a citable provision that
          carries clauses of its own — a section returned for a question its
          own stem answers only halfway (``"…except:"``).
        * otherwise it is ``fragment``'s **path parent** — a subsection or
          clause returned on its own, whose siblings state the other limbs of
          the same rule. This is the arm that matters most in practice: on the
          question that opened ABS-521 the ranker returned ``333 > (1.5)`` (the
          93.0 m² floor-area cap) and *not* the section, so completing upwards
          is the only route to the 60.0 m² footprint cap in ``(a)``.

        A container (Part, Schedule, Appendix, heading) is never a provision:
        "complete the provision" must not become "return the chapter".
        """
        if (
            fragment.fragment_type in PROVISION_TYPES
            and self._children_exist(fragment)
        ):
            return fragment
        parent = self.path_parent(fragment)
        if parent is None or parent.fragment_type in CONTAINER_TYPES:
            return None
        return parent

    def operative_clauses(
        self, fragment: SourceFragment, *, limit: int = OPERATIVE_CLAUSE_LIMIT
    ) -> tuple[list[SourceFragment], int]:
        """``(clauses, omitted)`` — the rest of ``fragment``'s provision.

        ``fragment`` itself is never in the list: a match does not need to be
        told its own text. ``omitted`` is how many clauses the cap dropped, and
        it is returned rather than swallowed so the caller can say so — a
        provision truncated in silence reads as a complete one.
        """
        provision = self.governing_provision(fragment)
        if provision is None:
            return [], 0
        siblings = [
            child
            for child in self.direct_path_children(
                provision.document_id, provision.citation_path
            )
            if child.id != fragment.id
        ]
        if len(siblings) <= limit:
            return siblings, 0
        return siblings[:limit], len(siblings) - limit

    def lineage(self, fragment: SourceFragment) -> list[SourceFragment]:
        """Strict ancestors of ``fragment``, root-first, by path *and* by tree.

        The union rather than either alone. The tree parent of s.333's clauses
        is the heading "Accessory Structure Footprint and Area", which is real
        context about what the clauses are for; the path parent is s.333, which
        is the sentence they finish. A reader needs both, and before ABS-521 the
        agent was shown only the first.
        """
        ordered: list[SourceFragment] = []
        seen_in_result: set[int] = set()

        def walk(current: SourceFragment, visiting: frozenset[int], depth: int) -> None:
            if depth >= _MAX_PATH_DEPTH:
                return
            parents: list[SourceFragment] = []
            tree_parent = current.parent
            if tree_parent is not None:
                parents.append(tree_parent)
            path_parent = self.path_parent(current)
            if path_parent is not None and all(
                path_parent.id != parent.id for parent in parents
            ):
                parents.append(path_parent)
            for parent in parents:
                if parent.id in visiting:
                    # A cycle in the tree, or a path that names its own
                    # ancestor. Missing scope, never an error.
                    continue
                walk(parent, visiting | {parent.id}, depth + 1)
                if parent.id not in seen_in_result:
                    seen_in_result.add(parent.id)
                    ordered.append(parent)

        walk(fragment, frozenset({fragment.id}), 0)
        return ordered

    # ------------------------------------------------------------------

    def _children_exist(self, fragment: SourceFragment) -> bool:
        return bool(
            self.direct_path_children(fragment.document_id, fragment.citation_path)
        )
