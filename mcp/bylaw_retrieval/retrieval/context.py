"""Provision-in-context: the scoping a clause's containers supply (ABS-492).

A by-law clause is written to be read under its heading. "(f) 2.5 metres
elsewhere." is a complete rule to a human holding the page and a five-token
fragment to a retriever holding only the row: the zone it applies in, the
dimension it constrains and the exception it carves out all live one, two or
three levels up the tree. 2,968 of the 7,100 fragments in the dev corpus carry
no ``citation_path`` at all, so for them the ancestor chain is the *only* place
scope is stated.

This module supplies that chain to the scorer. It deliberately does **not**
materialise a per-fragment ``context_text`` column:

* The text channel already SELECTs every in-scope fragment, so in the common
  (unfiltered) request the ancestors are *already loaded* — seeding the index
  from that result set costs zero extra queries. Only a request that narrows
  the scope (``attribute_tag_filter``, ``citation_path_prefix``, a page window)
  can leave ancestors outside it, and those are fetched one tree level at a
  time, which the corpus bounds tightly: mean ancestor depth 1.88, p95 4.
* A materialised column would be a second writer over data the tree already
  states, landing immediately after ABS-491 established "ORM-declared, single
  writer" and ABS-488 rewrote every citation path. A re-parent or a repath
  would stale it silently — the failure mode is a wrong ranking, not an error,
  which is the one thing a retrieval artifact must not produce quietly.
* Materialisation buys nothing while scoring is O(all in-scope fragments) in
  Python anyway. When retrieval moves to a real index, the context text becomes
  worth persisting — and it should be derived *in the indexer* at that point,
  not carried on the OLTP row until then.
"""
from __future__ import annotations

import re
from functools import lru_cache

from sqlalchemy import select

from layer1.db.base import SourceFragment

# A citation-path segment wrapped in square brackets is the container's own
# prose, not a citable step: ABS-488 repathed clauses onto the container that
# scopes them, so "Part V > 135 > [The maximum required side setback for any
# main building shall be] > (a)" carries a whole sentence inside the path.
# That sentence is context, and the scorer must weigh it as context — see
# ``_structural_citation_path`` in service.py for why it cannot stay in the
# path haystack.
_DESCRIPTIVE_SEGMENT_RE = re.compile(r"^\[(?P<body>.*)\]$", re.DOTALL)
_PATH_SEPARATOR = ">"


@lru_cache(maxsize=16384)
def split_citation_path(citation_path: str | None) -> tuple[str, str]:
    """Split a citation path into its structural and descriptive halves.

    Returns ``(structural, descriptive)``, both lower-cased for the token
    matcher. ``structural`` keeps the citable steps joined by the original
    separator so a path-shaped query still matches them; ``descriptive`` is the
    bracketed container prose, joined by spaces. Either may be empty.

    Cached because both the scorer and the context channel ask for the same
    fragment's split on every request, and a citation path is stable for the
    life of an ingest — the cache is sized above the corpus's 4,132 distinct
    paths so it stays warm across requests rather than only within one.
    """
    if not citation_path:
        return "", ""
    structural: list[str] = []
    descriptive: list[str] = []
    for raw_segment in citation_path.split(_PATH_SEPARATOR):
        segment = raw_segment.strip()
        if not segment:
            continue
        match = _DESCRIPTIVE_SEGMENT_RE.match(segment)
        if match:
            descriptive.append(match.group("body").strip())
        else:
            structural.append(segment)
    return (
        f" {_PATH_SEPARATOR} ".join(structural).lower(),
        " ".join(descriptive).lower(),
    )


class ContextNode:
    """The scoping text one ancestor contributes, and who its own parent is.

    ``haystack`` is built on first read, not on construction. The index holds a
    node per in-scope fragment (7,100 in the dev corpus) but only the ~1,600
    that are actually somebody's ancestor are ever asked for their text, so
    lower-casing every fragment's body up front would be work thrown away for
    three fragments in four.
    """

    __slots__ = ("fragment_id", "parent_fragment_id", "_label", "_text", "_haystack")

    def __init__(self, fragment: SourceFragment) -> None:
        self.fragment_id = fragment.id
        self.parent_fragment_id = fragment.parent_fragment_id
        # Label and text only. The ancestor's own citation_path is deliberately
        # excluded: its structural steps ("Part V", "229") already reach the
        # descendant through the descendant's own path, and re-scoring them
        # here would pay twice for one signal.
        self._label = fragment.citation_label or ""
        self._text = fragment.text
        self._haystack: str | None = None

    @property
    def haystack(self) -> str:
        if self._haystack is None:
            self._haystack = f"{self._label} {self._text}".strip().lower()
        return self._haystack


class AncestorIndex:
    """Ancestor chains for a fragment set, resolved without an N+1 walk.

    ``SourceFragment.parent`` is a lazy relationship, so walking it per
    fragment issues one SELECT per unknown ancestor per fragment. The scorer
    touches every in-scope fragment, so that walk is not affordable there.
    This loads the closure breadth-first — one query per tree level over the
    ancestors that are not already in hand — and answers ``chain()`` from
    memory afterwards.
    """

    #: Defence against a malformed tree (a parent cycle) turning the ascent
    #: into an infinite loop. The deepest real chain in the dev corpus is 19.
    _MAX_DEPTH = 64

    def __init__(self, nodes: dict[int, ContextNode]) -> None:
        self._nodes = nodes
        self._chains: dict[int, tuple[int, ...]] = {}

    @classmethod
    def build(cls, session, fragments) -> AncestorIndex:
        """Index ``fragments`` plus every ancestor they reach.

        The supplied fragments seed the index, so the unfiltered request —
        where the scope already *is* the whole document — resolves every
        ancestor without a single additional query.
        """
        nodes = {fragment.id: ContextNode(fragment) for fragment in fragments}
        frontier = {
            node.parent_fragment_id
            for node in nodes.values()
            if node.parent_fragment_id is not None and node.parent_fragment_id not in nodes
        }
        depth = 0
        while frontier and depth < cls._MAX_DEPTH:
            parents = (
                session.execute(
                    select(SourceFragment).where(SourceFragment.id.in_(frontier))
                )
                .scalars()
                .all()
            )
            if not parents:
                break
            for parent in parents:
                nodes[parent.id] = ContextNode(parent)
            frontier = {
                node.parent_fragment_id
                for node in (nodes[parent.id] for parent in parents)
                if node.parent_fragment_id is not None
                and node.parent_fragment_id not in nodes
            }
            depth += 1
        return cls(nodes)

    def node(self, fragment_id: int) -> ContextNode | None:
        return self._nodes.get(fragment_id)

    def chain(self, fragment_id: int) -> tuple[int, ...]:
        """Strict ancestors of ``fragment_id``, nearest parent first.

        A fragment whose parent was deleted (``ON DELETE SET NULL``) or whose
        parent sits outside the loaded closure simply has a shorter chain —
        an unresolvable ancestor is missing scope, never an error.
        """
        cached = self._chains.get(fragment_id)
        if cached is not None:
            return cached
        chain: list[int] = []
        seen = {fragment_id}
        current = self._nodes.get(fragment_id)
        while current is not None and current.parent_fragment_id is not None:
            parent_id = current.parent_fragment_id
            if parent_id in seen or len(chain) >= self._MAX_DEPTH:
                break
            seen.add(parent_id)
            chain.append(parent_id)
            current = self._nodes.get(parent_id)
        resolved = tuple(chain)
        self._chains[fragment_id] = resolved
        return resolved
