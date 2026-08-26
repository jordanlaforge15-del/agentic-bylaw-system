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

The second population: a path parent that names nothing (ABS-523)
-----------------------------------------------------------------
ABS-521's audit counted two buckets and fixed one. The other — **1,208
fragments in doc 4 whose parent path names no fragment at all** — is s.233(3)::

    233 (1) … (a) a building width of 20.0 metres; and
              (b) a building depth of 30.0 metres.
        An addition to an existing main building shall only be permitted in the
        rear yard … if the addition causes the main building to contain
              (a) more than 2 dwelling units in an ER-2 zone; or
              (b) more than 8 dwelling units in an ER-3 zone.

The stem in the middle was never given a ``citation_path``. Its two clauses
were, and it reads ``Part V > 233 > [An addition to an existing main building
... main building to contain] > (b)`` — a bracketed segment quoting a stem that
is not itself addressable. So ``path_parent`` missed, ``governing_provision``
returned ``None``, and the clause arrived as the four words "more than 8
dwelling units in an ER-3 zone" with no stem, no sibling, and no section. The
model that retrieved it invented a reading — "s.233(3) enables site plan
approval … it does not override the 8-unit cap" — and told a developer to seek
a rezoning the by-law does not require (TC-023).

The bracketed segment is not a description of the stem, it *is* the stem:
:func:`layer1.pipeline.citation_repath.context_segment` is a pure function of
the stem's text, so the segment identifies its fragment exactly. That makes the
lookup content-addressed rather than heuristic, and it resolves all 1,005 such
clauses across docs 4 and 5 — none unmatched, the furthest 54 fragments back in
reading order.

What the module does not do
---------------------------
It does not re-parent anything and it does not touch a score. Ranking is
unchanged by design: the fix is that whatever the ranker returns arrives
*complete*, which is a payload guarantee, not an ordering one. The regression
guard for that distinction is that ``evals/retrieval/BASELINE.json`` moves only
by its fingerprint when this lands, never by a metric.
"""
from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from layer1.db.base import SourceFragment
from layer1.models.enums import FragmentType
from layer1.pipeline.citation_repath import context_segment

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

#: How far back in reading order the stem of a dangling bracketed container may
#: sit before the search gives up (ABS-523). Measured, not guessed: every one of
#: the 1,005 dangling-bracketed clauses across docs 4 and 5 resolves, 95% within
#: 20 fragments and the furthest at 54 — a stem introducing a long list. The
#: window is what keeps a *malformed* segment from scanning a whole by-law.
STEM_SEARCH_WINDOW = 128


def _is_bracketed_segment(citation_path: str | None) -> bool:
    """True when a path's last segment is quoted container prose, ``[like this]``.

    ABS-488 puts a context container's own words into the path when it has no
    enumerator of its own. Those are the segments that can name a fragment the
    ingest never pathed; a plain ``233`` or ``(a)`` that resolves to nothing is
    a missing fragment, not a recoverable one.
    """
    normalised = normalise_citation_path(citation_path)
    if normalised is None:
        return False
    segment = normalised.split(f" {_PATH_SEPARATOR} ")[-1]
    return segment.startswith("[") and segment.endswith("]")


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
        #: (document_id, normalised_path) -> bracketed child paths one segment
        #: below that name no fragment (ABS-523). Falls out of the same prefix
        #: scan that produces ``_children``, so it costs no extra query.
        self._dangling: dict[tuple[int, str], tuple[str, ...]] = {}
        #: (document_id, dangling container path) -> the stem fragment's id, or
        #: None when nothing in the search window matches the segment.
        self._stems: dict[tuple[int, str], int | None] = {}

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

        ABS-523: when the parent path names nothing because its last segment is
        bracketed container prose — a stem the ingest quoted into the path but
        never gave a path of its own — the stem is recovered by content. For
        1,005 clauses in docs 4 and 5 this is the only route to the sentence
        they finish.
        """
        parent_path = parent_citation_path(fragment.citation_path)
        direct = self.fragment_at_path(fragment.document_id, parent_path)
        if direct is not None:
            return direct
        return self.stem_of_container(fragment.document_id, parent_path)

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
        key = self._scan(document_id, normalised)
        return self._fragments(self._children[key])

    def dangling_container_paths(
        self, document_id: int, citation_path: str | None
    ) -> list[str]:
        """Bracketed paths one segment below ``citation_path`` that name nothing.

        ABS-523. ``Part V > 233`` has two clauses of its own and a third limb —
        the rear-addition rule — whose stem was never pathed, so its clauses sit
        one level further down under a bracketed segment. Nothing in the path
        tree stands at that segment, which is exactly why the limb was invisible
        to a caller enumerating the section's children.
        """
        normalised = normalise_citation_path(citation_path)
        if normalised is None:
            return []
        key = self._scan(document_id, normalised)
        return list(self._dangling[key])

    def stem_of_container(
        self, document_id: int, container_path: str | None
    ) -> SourceFragment | None:
        """The fragment a dangling bracketed container path quotes, if any.

        ``context_segment`` is a pure function of a fragment's text, so the
        segment ``[An addition to an existing main building ... main building to
        contain]`` names its stem exactly — this is an identity check, not a
        similarity one. Where two stems in a document share a segment (repeated
        boilerplate: 203 of the 1,005 cases), the nearest one *preceding* the
        container's first clause is the one that introduces it.

        Returns ``None`` for a path that resolves normally, for a segment that
        is not bracketed prose, and for a container whose stem is not in the
        window — in every case the caller falls back to what it did before.
        """
        normalised = normalise_citation_path(container_path)
        if normalised is None:
            return None
        segment = normalised.split(f" {_PATH_SEPARATOR} ")[-1]
        if not _is_bracketed_segment(normalised):
            return None
        if self.fragment_at_path(document_id, normalised) is not None:
            return None  # the path resolves; nothing is dangling
        key = (document_id, normalised)
        if key not in self._stems:
            children = self.direct_path_children(document_id, normalised)
            self._stems[key] = (
                self._find_stem(document_id, segment, children[0].reading_order_start)
                if children
                else None
            )
        stem_id = self._stems[key]
        return None if stem_id is None else self._session.get(SourceFragment, stem_id)

    def provision_children(
        self, document_id: int, citation_path: str | None
    ) -> list[SourceFragment]:
        """Direct path children, with dangling containers expanded in place.

        A dangling container is not a citable child — nothing stands at its
        path — but its stem and clauses are limbs of the same provision as the
        children beside them, and s.233's rear-addition rule reaching a reader
        depends on being enumerated here. Results are in reading order, so an
        expanded limb lands where the by-law prints it rather than appended at
        the end.
        """
        normalised = normalise_citation_path(citation_path)
        if normalised is None:
            return []
        collected: dict[int, SourceFragment] = {
            child.id: child
            for child in self.direct_path_children(document_id, normalised)
        }
        for container in self.dangling_container_paths(document_id, normalised):
            stem = self.stem_of_container(document_id, container)
            if stem is not None:
                collected.setdefault(stem.id, stem)
            for child in self.direct_path_children(document_id, container):
                collected.setdefault(child.id, child)
        return sorted(
            collected.values(), key=lambda node: (node.reading_order_start, node.id)
        )

    # ------------------------------------------------------------------

    def _scan(self, document_id: int, normalised: str) -> tuple[int, str]:
        """Populate ``_children`` and ``_dangling`` for a path from one query."""
        key = (document_id, normalised)
        if key in self._children:
            return key
        prefix = f"{normalised} {_PATH_SEPARATOR} "
        escaped = prefix.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        candidates = (
            self._session.execute(
                select(SourceFragment)
                .where(
                    SourceFragment.document_id == document_id,
                    SourceFragment.citation_path.like(f"{escaped}%", escape="\\"),
                )
                .order_by(SourceFragment.reading_order_start, SourceFragment.id)
            )
            .scalars()
            .all()
        )
        depth = path_depth(normalised)
        children: list[int] = []
        child_paths: set[str] = set()
        grandchild_parents: list[str] = []
        for candidate in candidates:
            candidate_path = normalise_citation_path(candidate.citation_path)
            if candidate_path is None or candidate_path == normalised:
                continue
            candidate_depth = path_depth(candidate_path)
            if candidate_depth == depth + 1:
                children.append(candidate.id)
                child_paths.add(candidate_path)
            elif candidate_depth == depth + 2:
                parent = parent_citation_path(candidate_path)
                if parent is not None and parent not in grandchild_parents:
                    grandchild_parents.append(parent)
        self._children[key] = tuple(children)
        self._dangling[key] = tuple(
            parent
            for parent in grandchild_parents
            if parent not in child_paths
            and parent.split(f" {_PATH_SEPARATOR} ")[-1].startswith("[")
        )
        return key

    def _find_stem(
        self, document_id: int, segment: str, before: int
    ) -> int | None:
        candidates = (
            self._session.execute(
                select(SourceFragment)
                .where(
                    SourceFragment.document_id == document_id,
                    SourceFragment.reading_order_start < before,
                )
                .order_by(SourceFragment.reading_order_start.desc(), SourceFragment.id.desc())
                .limit(STEM_SEARCH_WINDOW)
            )
            .scalars()
            .all()
        )
        for candidate in candidates:
            if context_segment(candidate.text or "") == segment:
                return candidate.id
        return None

    def _fragments(self, ids: tuple[int, ...]) -> list[SourceFragment]:
        return [
            fragment
            for fragment in (
                self._session.get(SourceFragment, fragment_id) for fragment_id in ids
            )
            if fragment is not None
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

        ABS-523 adds a third way the second arm can land: the path parent may
        name no fragment because it is a bracketed stem the ingest never pathed.
        ``path_parent`` recovers it by content, so ``(b) more than 8 dwelling
        units in an ER-3 zone.`` now reports the sentence it finishes.
        """
        reference = self._provision_ref(fragment)
        return reference.fragment if reference is not None else None

    def operative_clauses(
        self, fragment: SourceFragment, *, limit: int = OPERATIVE_CLAUSE_LIMIT
    ) -> tuple[list[SourceFragment], int]:
        """``(clauses, omitted)`` — the rest of ``fragment``'s provision.

        ``fragment`` itself is never in the list: a match does not need to be
        told its own text. ``omitted`` is how many clauses the cap dropped, and
        it is returned rather than swallowed so the caller can say so — a
        provision truncated in silence reads as a complete one.
        """
        reference = self._provision_ref(fragment)
        if reference is None:
            return [], 0
        clauses = self.provision_children(
            fragment.document_id, reference.children_path
        )
        if reference.through_container and reference.fragment is not None:
            # ABS-523: the stem goes in the list, unlike the ordinary upward
            # case where the provision is a section a caller can look up by
            # path. This stem has no citation path at all, so completion is the
            # only route to it — and it is not scope, it is the first half of
            # the clause's sentence. Reading order puts it in front.
            clauses = sorted(
                {clause.id: clause for clause in [*clauses, reference.fragment]}.values(),
                key=lambda node: (node.reading_order_start, node.id),
            )
        siblings = [clause for clause in clauses if clause.id != fragment.id]
        if len(siblings) <= limit:
            return siblings, 0
        return siblings[:limit], len(siblings) - limit

    def _provision_ref(self, fragment: SourceFragment) -> "_ProvisionRef | None":
        """``governing_provision`` plus the path whose children complete it.

        The two are not the same string once a dangling container is in play:
        the provision is the stem, whose ``citation_path`` is null, while its
        limbs are addressed under the bracketed segment that quotes it.
        """
        if (
            fragment.fragment_type in PROVISION_TYPES
            and self._children_exist(fragment)
        ):
            return _ProvisionRef(
                fragment=fragment,
                children_path=normalise_citation_path(fragment.citation_path) or "",
                through_container=False,
            )
        parent_path = parent_citation_path(fragment.citation_path)
        if parent_path is None:
            return None
        direct = self.fragment_at_path(fragment.document_id, parent_path)
        if direct is not None:
            if direct.fragment_type in CONTAINER_TYPES:
                return None
            return _ProvisionRef(
                fragment=direct, children_path=parent_path, through_container=False
            )
        if not _is_bracketed_segment(parent_path):
            # A parent path naming a section that was never extracted (203 such
            # fragments across docs 4 and 5). There is nothing to recover by
            # content, and guessing would invent lineage rather than find it.
            return None
        stem = self.stem_of_container(fragment.document_id, parent_path)
        if stem is None and not self._has_sibling(fragment, parent_path):
            return None
        # The stem may be absent and the siblings still belong together: they
        # are limbs of one list, addressed under one segment. ``fragment`` is
        # then None and ``governing_provision`` says so, but completion still
        # runs sideways.
        return _ProvisionRef(
            fragment=stem, children_path=parent_path, through_container=True
        )

    def _has_sibling(self, fragment: SourceFragment, container_path: str) -> bool:
        return any(
            child.id != fragment.id
            for child in self.direct_path_children(
                fragment.document_id, container_path
            )
        )

    def lineage(self, fragment: SourceFragment) -> list[SourceFragment]:
        """Strict ancestors of ``fragment`` in document order, by path *and* tree.

        The union rather than either alone. The tree parent of s.333's clauses
        is the heading "Accessory Structure Footprint and Area", which is real
        context about what the clauses are for; the path parent is s.333, which
        is the sentence they finish. A reader needs both, and before ABS-521 the
        agent was shown only the first.

        Ordered by ``reading_order_start`` rather than by how the walk happened
        to reach each node. A container is printed before what it contains, so
        for a well-formed document that *is* root-first — and unlike "the order
        the ascent produced" it stays stable when the two lineages disagree
        about the shape of the tree, which is precisely the case this method
        exists for.
        """
        collected: dict[int, SourceFragment] = {}
        frontier: list[SourceFragment] = [fragment]
        depth = 0
        while frontier and depth < _MAX_PATH_DEPTH:
            parents: dict[int, SourceFragment] = {}
            for current in frontier:
                for parent in (current.parent, self.path_parent(current)):
                    if parent is None or parent.id == fragment.id:
                        # A parent pointing back at the fragment itself is a
                        # malformed tree, or a path naming its own descendant.
                        # Missing scope, never an error.
                        continue
                    if parent.id in collected or parent.id in parents:
                        continue
                    parents[parent.id] = parent
            collected.update(parents)
            frontier = list(parents.values())
            depth += 1
        return sorted(
            collected.values(), key=lambda node: (node.reading_order_start, node.id)
        )

    # ------------------------------------------------------------------

    def _children_exist(self, fragment: SourceFragment) -> bool:
        # Expanded rather than direct: a subsection whose only limbs sit under a
        # dangling bracketed stem does carry clauses — they are just not
        # addressable one segment down (ABS-523).
        return bool(
            self.provision_children(fragment.document_id, fragment.citation_path)
        )


@dataclass(frozen=True)
class _ProvisionRef:
    """A governing provision and where its remaining limbs are addressed."""

    #: None when the provision was reached through a bracketed segment whose
    #: stem is not in the corpus — the limbs are still each other's completion.
    fragment: SourceFragment | None
    children_path: str
    #: True when the provision was reached through a bracketed path segment that
    #: names no fragment — the ABS-523 population. The stem is then part of the
    #: completion rather than something the caller can cite for itself.
    through_container: bool
