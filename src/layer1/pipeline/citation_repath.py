"""Put the missing discriminator back into a citation path (ABS-488).

Two shapes of citation path in this corpus cannot be cited, because two or more
provisions compute the identical string and ``_clear_duplicate_citation_paths``
blanks every one of them:

**Clauses lose the container that scopes them.** The builder decorated a
clause's path with the *sticky* heading that was last seen — not with the
subsection, list stem or definition the clause actually sits under — so section
9's two clause groups both computed
``Part I > 9 > [Development Permit Exemptions] > (a)``.

**Parts lose their chapter.** All eight of Part I's chapter headings parse as
label ``Part I``, so all eight collide on the bare ``Part I``.

This module is the single definition of the repaired shape. It is deliberately
a pure function over an *ordered sequence of fragment-like records* rather than
a method on the builder, because two callers need the identical answer:

* :func:`layer1.pipeline.hierarchy.reconstruct_hierarchy`, at ingest time, over
  freshly built :class:`~layer1.models.schemas.FragmentData`; and
* ``scripts/repath_citation_paths.py``, over ``source_fragment`` rows already in
  a database — the corpus is repaired by migration, never by re-ingest (see
  ``docs/data-gaps/abs461-production-impact.md`` for why re-ingest is the wrong
  instrument).

Both record types expose ``fragment_type``, ``citation_label``,
``citation_path`` and ``text``, which is all :class:`RepathNode` asks for.

## The rule

Fragments carry an implicit level: Part/Schedule/Appendix 1, Section 2,
Subsection 3, *context containers* (heading, prose, list item, footnote) 4,
Clause 5, Subclause 6. Walking the document in reading order with a
pop-to-level stack, a fragment's path parent is the nearest preceding fragment
of a strictly lower level:

* a **context container** anchors to the nearest structural fragment (level
  <= 3) and contributes a bracketed segment derived from its own text, so the
  list stem "On a registered heritage property ... a permit shall be required
  for:" becomes a real scope rather than being skipped over;
* a **clause** anchors to that container when one has intervened, and directly
  to the section/subsection when none has;
* a **subclause** anchors to the clause above it.

Two exceptions keep that from over-firing, both of them things this corpus
actually does:

*Wrapped body text.* Prose frequently interrupts a clause list, and treating
that as a new scope would push ``(b)`` under a container its sibling ``(a)``
does not share. So an intervening container is only adopted when the clause
**restarts** its enumeration — ``(r)`` then ``(a)`` opens a new group, ``(a)``
then ``(b)`` continues the old one.

*Deeper lists the label parser flattens.* ``(i)`` reads as the ninth letter as
readily as the first Roman numeral, and ``(A)`` case-folds onto ``(a)``, so a
fourth-level list arrives labelled like a second-level one. When a low-level
fragment follows another and neither continues the other's sequence,
:func:`_opens_nested_list` reads it as opening a list one level deeper —
``198 > (a) > (i)``, not a second ``198 > (i)``.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Protocol, Sequence

from layer1.models.enums import FragmentType
from layer1.pipeline.citations import citation_path

# "Part I, Chapter 2: Development Permit" -> the chapter is everything the Part
# regex left in the title. Anchored, so a title that merely mentions a chapter
# further along ("... as set out in Chapter 4") is not mistaken for one.
CHAPTER_TITLE_RE = re.compile(
    r"^\s*[,:;\-–—]?\s*(chapter\s+(?:\d+[A-Za-z]?|[IVXLCDM]+))\b",
    re.IGNORECASE,
)

# A simple enumerator — "(a)", "(iv)". Compound labels ("499(94)(f)") already
# carry their own discriminator and are left exactly as the builder wrote them.
SIMPLE_ENUMERATOR_RE = re.compile(r"^\(([0-9A-Za-z]{1,4})\)$")

# The enumerator as the *page* rendered it, before ``parse_citation_label``
# case-folds it. "(A)" and "(a)" are different lists in a by-law; the stored
# ``citation_label`` cannot tell them apart, the text can.
TEXT_ENUMERATOR_RE = re.compile(r"^\s*\(([0-9A-Za-z]{1,4})\)")

ALPHA_ENUMERATOR_RE = re.compile(r"^[a-z]{1,3}$")
ROMAN_ENUMERATOR_RE = re.compile(r"^[ivxlcdm]+$")

_ROMAN_VALUES = {"i": 1, "v": 5, "x": 10, "l": 50, "c": 100, "d": 500, "m": 1000}

#: Roman numerals that are also single letters. A clause labelled with one of
#: these is the ambiguous case :func:`_opens_nested_list` arbitrates.
AMBIGUOUS_ROMAN_TOKENS = {"i", "v", "x"}

#: How much of a container's text survives into the path segment. Long enough
#: that two stems in the same section stay distinguishable, short enough that a
#: clause path stays well inside ``source_fragment.citation_path``'s 1000 chars.
MAX_CONTEXT_SEGMENT_CHARS = 80
#: Split of that budget when the text is too long to carry whole. By-law stems
#: routinely share a long opening ("Where a non-conforming use in a structure
#: exists, ..."), so a head-only truncation collides where the full texts do
#: not; keeping a tail as well distinguishes them.
CONTEXT_SEGMENT_HEAD_CHARS = 46
CONTEXT_SEGMENT_TAIL_CHARS = 28
CONTEXT_SEGMENT_ELLIPSIS = " ... "

STRUCTURAL_LEVEL = 3
CONTEXT_LEVEL = 4
CLAUSE_LEVEL = 5
SUBCLAUSE_LEVEL = 6

FRAGMENT_LEVELS: dict[FragmentType, int] = {
    FragmentType.PART: 1,
    FragmentType.SCHEDULE: 1,
    FragmentType.APPENDIX: 1,
    FragmentType.SECTION: 2,
    FragmentType.SUBSECTION: 3,
    FragmentType.CLAUSE: CLAUSE_LEVEL,
    FragmentType.SUBCLAUSE: SUBCLAUSE_LEVEL,
}

LOW_LEVEL_FRAGMENT_TYPES = {FragmentType.CLAUSE, FragmentType.SUBCLAUSE}


class RepathNode(Protocol):
    """The slice of a fragment this module reads.

    Satisfied by both ``FragmentData`` (ingest) and ``SourceFragment`` (an
    already-persisted corpus), which is the whole point of the protocol.
    """

    fragment_type: FragmentType
    citation_label: str | None
    citation_path: str | None
    text: str


@dataclass
class _StackEntry:
    level: int
    path: str | None


@dataclass
class _Enumerated:
    """A low-level fragment as the walk has resolved it."""

    level: int
    token: str
    is_upper: bool
    parent_path: str | None


def part_label_with_chapter(label: str, title: str) -> str:
    """Fold a chapter designator out of a Part's title and into its label.

    ``("Part I", ", Chapter 2: Development Permit")`` -> ``"Part I, Chapter 2"``.
    A Part heading with no chapter is returned untouched, so ``Part I:
    Administration`` stays ``Part I`` — and, critically, so do the thousands of
    section paths that already read ``Part I > 9``. The chapter discriminates
    the *chapter heading's own* citation; it is not pushed onto descendants.
    """
    match = CHAPTER_TITLE_RE.match(title or "")
    if not match:
        return label
    chapter = " ".join(match.group(1).split())
    return f"{label}, {chapter[:1].upper()}{chapter[1:]}"


def _trim(text: str) -> str:
    return text.rstrip(" :;,.-")


def context_segment(text: str) -> str | None:
    """Derive the bracketed path segment a context container contributes.

    Content-addressed on purpose: an ordinal suffix (``(a)#2``) would be neither
    the provision's real citation nor stable across a re-ingest that renumbers
    fragments, whereas the stem's own words move with the text.
    """
    cleaned = _trim(" ".join((text or "").replace("\x00", "").split()))
    if not cleaned:
        return None
    if len(cleaned) > MAX_CONTEXT_SEGMENT_CHARS:
        cleaned = f"{_clip(cleaned, CONTEXT_SEGMENT_HEAD_CHARS)}{CONTEXT_SEGMENT_ELLIPSIS}{_clip_tail(cleaned, CONTEXT_SEGMENT_TAIL_CHARS)}"
    return f"[{cleaned}]" if cleaned else None


def _clip(text: str, limit: int) -> str:
    head = text[:limit]
    cut = head.rfind(" ")
    return _trim(head[:cut] if cut > limit // 2 else head)


def _clip_tail(text: str, limit: int) -> str:
    tail = text[-limit:]
    cut = tail.find(" ")
    return (tail[cut + 1 :] if 0 <= cut < limit // 2 else tail).lstrip()


def fragment_level(fragment_type: FragmentType) -> int:
    return FRAGMENT_LEVELS.get(fragment_type, CONTEXT_LEVEL)


def _enumerator(label: str | None) -> str | None:
    match = SIMPLE_ENUMERATOR_RE.match((label or "").strip())
    return match.group(1) if match else None


def _rendered_enumerator_is_upper(text: str) -> bool:
    match = TEXT_ENUMERATOR_RE.match(text or "")
    return bool(match) and match.group(1).isupper() and match.group(1).isalpha()


def _alpha_rank(token: str) -> int:
    rank = 0
    for char in token.lower():
        rank = rank * 26 + (ord(char) - ord("a") + 1)
    return rank


def _roman_rank(token: str) -> int:
    total = 0
    highest = 0
    for char in reversed(token.lower()):
        value = _ROMAN_VALUES[char]
        total += value if value >= highest else -value
        highest = max(highest, value)
    return total


def _rank(fragment_type: FragmentType, token: str) -> int | None:
    """Order ``token`` within the alphabet its fragment type enumerates in."""
    if fragment_type == FragmentType.SUBCLAUSE:
        return _roman_rank(token) if ROMAN_ENUMERATOR_RE.fullmatch(token.lower()) else None
    return _alpha_rank(token) if ALPHA_ENUMERATOR_RE.fullmatch(token.lower()) else None


def _advances(fragment_type: FragmentType, previous: str, current: str) -> bool:
    """True when ``current`` carries the list ``previous`` opened forward.

    Deliberately "advances" rather than "is the immediate successor": a renderer
    that drops ``(iii)`` and ``(iv)`` should not turn ``(v)`` into a new list.
    """
    previous_rank, current_rank = _rank(fragment_type, previous), _rank(fragment_type, current)
    if previous_rank is None or current_rank is None:
        return False
    return current_rank > previous_rank


def _opens_nested_list(fragment_type: FragmentType, token: str, is_upper: bool, previous: _Enumerated) -> bool:
    """True when this fragment starts a list one level below ``previous``.

    Both signals are artefacts of a flat label parser meeting a four-deep
    enumeration — ``(1)(a)(i)(A)``:

    * the page rendered ``(A)`` where the previous sibling was ``(a)``; case is
      the only thing separating those two lists, and ``citation_label`` has
      already discarded it; and
    * a clause labelled ``(i)`` following ``(a)`` is the first Roman numeral of
      a sub-list, not the ninth letter of the one it interrupts. Following
      ``(h)`` it is exactly the ninth letter, so the immediate-successor test is
      what tells the two apart.
    """
    if is_upper != previous.is_upper:
        return True
    if fragment_type == FragmentType.CLAUSE and token.lower() in AMBIGUOUS_ROMAN_TOKENS:
        return _alpha_rank(token) != _alpha_rank(previous.token) + 1
    return False


def _effective_level(
    fragment_type: FragmentType, token: str, is_upper: bool, previous: _Enumerated | None
) -> int:
    nominal = fragment_level(fragment_type)
    if previous is None:
        return nominal
    if _opens_nested_list(fragment_type, token, is_upper, previous):
        return previous.level + 1
    if nominal > previous.level:
        return nominal
    if _advances(fragment_type, previous.token, token):
        return previous.level
    return nominal


def repath_low_level_fragments(nodes: Sequence[RepathNode]) -> list[str | None]:
    """Recompute clause and subclause paths from the container that scopes them.

    Returns a list parallel to ``nodes``: every non-low-level fragment keeps the
    ``citation_path`` it arrived with, so a caller can apply the result blindly.
    Fragments whose label is compound (``499(94)(f)``) also keep theirs — the
    label already spells out the full address, and rewriting it under a
    container would say the same thing twice.
    """
    results: list[str | None] = [node.citation_path for node in nodes]
    stack: list[_StackEntry] = []
    # The previous low-level fragment, and the previous sibling seen at each
    # level. The first drives nesting, the second drives the restart test.
    previous_low: _Enumerated | None = None
    previous_sibling: dict[int, _Enumerated] = {}

    for index, node in enumerate(nodes):
        nominal_level = fragment_level(node.fragment_type)
        token = _enumerator(node.citation_label) if nominal_level >= CLAUSE_LEVEL else None

        if nominal_level <= STRUCTURAL_LEVEL or token is None:
            # Structural fragments, context containers, and low-level fragments
            # whose label the simple-enumerator rule does not recognise all land
            # here: none of them is repathed, they only shape what follows.
            level = nominal_level
            path = results[index]
            if level == CONTEXT_LEVEL:
                while stack and stack[-1].level >= level:
                    stack.pop()
                anchor = stack[-1].path if stack else None
                segment = context_segment(node.text)
                path = citation_path(anchor, segment) if anchor and segment else None
            if level <= STRUCTURAL_LEVEL:
                previous_sibling.clear()
            if level < CLAUSE_LEVEL:
                previous_low = None
            while stack and stack[-1].level >= level:
                stack.pop()
            stack.append(_StackEntry(level, path))
            continue

        is_upper = _rendered_enumerator_is_upper(node.text)
        level = _effective_level(node.fragment_type, token, is_upper, previous_low)

        while stack and stack[-1].level >= level:
            stack.pop()
        parent_path = stack[-1].path if stack else None

        sibling = previous_sibling.get(level)
        if (
            sibling is not None
            and sibling.parent_path is not None
            and sibling.parent_path != parent_path
            and _advances(node.fragment_type, sibling.token, token)
        ):
            # A container wrapped mid-list; ``(b)`` belongs beside ``(a)``.
            parent_path = sibling.parent_path

        new_path = citation_path(parent_path, node.citation_label) if parent_path else None
        results[index] = new_path
        resolved = _Enumerated(level=level, token=token, is_upper=is_upper, parent_path=parent_path)
        previous_low = resolved
        previous_sibling[level] = resolved
        stack.append(_StackEntry(level, new_path))

    return results
