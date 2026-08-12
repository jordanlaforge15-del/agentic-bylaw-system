"""ABS-471: the zone-appropriateness rule for eval-case citations.

``scripts/build_bylaw_reference_index.py`` answers "does this citation exist?".
It never answered "is this citation *for this case's zone*?", and every
by-law-reference defect ABS-470 fixed lived in that gap: ``Section 196`` and
``Section 200`` (the HR chapter) cited on INS, DD, COR and CDD-2 cases,
``Section 111`` (DD) on a DH case, ``Section 344`` (the Schmidtville HCD, an
HCD-SV chapter) on an ER-3 backyard-suite case, ``Table 1A`` on an RPK case.

The rule is one sentence: **a section governs only the zones its chapter names,
and a permitted-use table covers only the zones in its caption.** Both halves
come from ``evals/regional_centre_zone_chapter_map.json``, derived from the
corpus by ``scripts/build_zone_chapter_map.py`` — so this module holds the
*rule*, never the data, and cannot drift from the by-law on its own.

It is deliberately pure and database-free. Three callers share it:

* ``scripts/build_bylaw_reference_index.py --check`` — G3, over
  ``expected_bylaw_references``.
* ``tests/test_eval_keyword_chapters.py`` — G2, over
  ``expected_answer_keywords``, plus the reference axis again offline.
* ``scripts/verify_eval_corpus_integrity.py`` — the operator-facing CLI.

**What it does not check.** A bare number keyword (``6.0 m``, ``80%``) is not
tied to any clause here. Three cases in the pre-ABS-470 file asserted
lot-coverage percentages against sections reading "No maximum required lot
coverage applies", and no mechanical rule available to us would have caught
that. See docs/ABS-471-EVAL-CORPUS-GUARDS.md.
"""
from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any

# The reference grammar of build_bylaw_reference_index.py, relaxed from
# fullmatch to search so it also finds a citation *inside* a keyword string.
# Keywords are usually bare tokens ("Section 254"), but nothing forces that and
# a guard that only matched whole strings would quietly skip the rest.
SECTION_TOKEN_RE = re.compile(r"\bSection (\d+[A-Z]?)((?:\(\w+\))*)")
TABLE_TOKEN_RE = re.compile(r"\bTable (\d+[A-Z]?)\b")
SCHEDULE_TOKEN_RE = re.compile(r"\bSchedule (\d+[A-Z]?)\b")


@dataclass(frozen=True)
class Citation:
    """One ``Section N`` / ``Table N`` / ``Schedule N`` token found in a field."""

    kind: str  # "section" | "table" | "schedule"
    text: str  # the token as written, e.g. "Section 9(1)(c)"
    label: str  # the reference-index label, e.g. "Section 9"
    number: int | None  # the section number, for the chapter lookup


def citations_in(values: Iterable[str]) -> list[Citation]:
    """Every by-law citation named anywhere in ``values``, in order.

    Deduplicated on the token text: a keyword list that repeats ``Section 200``
    should not produce the same failure twice.
    """
    found: list[Citation] = []
    seen: set[str] = set()

    def add(citation: Citation) -> None:
        if citation.text not in seen:
            seen.add(citation.text)
            found.append(citation)

    for value in values:
        if not isinstance(value, str):
            continue
        for match in SECTION_TOKEN_RE.finditer(value):
            number, groups = match.group(1), match.group(2)
            add(
                Citation(
                    kind="section",
                    text=f"Section {number}{groups}",
                    label=f"Section {number}",
                    number=int(number) if number.isdigit() else None,
                )
            )
        for match in TABLE_TOKEN_RE.finditer(value):
            add(
                Citation(
                    kind="table",
                    text=f"Table {match.group(1)}",
                    label=f"Table {match.group(1)}",
                    number=None,
                )
            )
        for match in SCHEDULE_TOKEN_RE.finditer(value):
            add(
                Citation(
                    kind="schedule",
                    text=f"Schedule {match.group(1)}",
                    label=f"Schedule {match.group(1)}",
                    number=None,
                )
            )
    return found


def chapters_for_section(number: int, chapter_map: dict[str, Any]) -> list[dict[str, Any]]:
    """Every chapter that contains section ``number``.

    Normally one. It can be more: the ingest labels one Part XVI fragment "7",
    colliding with Part I's Section 7. Returning the list rather than a single
    chapter lets the caller stay silent on a number it cannot place — see
    :func:`zone_violation`.
    """
    return [
        chapter
        for chapter in chapter_map.get("chapters", [])
        if number in chapter.get("sections", ())
    ]


def zones_governing_section(number: int, chapter_map: dict[str, Any]) -> list[str] | None:
    """The zones section ``number`` governs, or None if it governs all of them.

    None — "unconstrained" — is the answer for a general provision (Part I
    administration, Part XIII parking, Part V Chapter 19 accessory structures),
    and for a number the corpus places in more than one chapter.
    """
    chapters = chapters_for_section(number, chapter_map)
    if len(chapters) != 1:
        return None
    zones = chapters[0].get("zones") or []
    return zones or None


def zone_violation(
    zone: str, citation: Citation, chapter_map: dict[str, Any]
) -> str | None:
    """Why ``citation`` cannot belong to a case in ``zone``, or None if it can.

    The returned string names the token, the zone the case claims, and the
    zones the provision actually governs — the three things a reader needs to
    decide whether the citation or the zone is the thing that is wrong.
    """
    if citation.kind == "table":
        covered = chapter_map.get("permitted_use_tables", {}).get(citation.label)
        if covered is None or zone in covered:
            return None
        return (
            f"{citation.text} covers {', '.join(covered)} — not {zone}. "
            f"The permitted-use table for {zone} is "
            f"{_table_for_zone(zone, chapter_map) or 'not published'}."
        )

    if citation.kind != "section" or citation.number is None:
        return None

    chapters = chapters_for_section(citation.number, chapter_map)
    if len(chapters) != 1:
        return None
    chapter = chapters[0]
    zones = chapter.get("zones") or []
    if not zones or zone in zones:
        return None
    return (
        f"{citation.text} is in Part {chapter['part']}, Chapter "
        f"{chapter['chapter']} (sections {chapter['first_section']}-"
        f"{chapter['last_section']}), which governs {', '.join(zones)} — "
        f"not {zone}"
    )


def _table_for_zone(zone: str, chapter_map: dict[str, Any]) -> str | None:
    for label, zones in sorted(chapter_map.get("permitted_use_tables", {}).items()):
        if zone in zones:
            return label
    return None


def case_violations(
    case: dict[str, Any], field: str, chapter_map: dict[str, Any]
) -> list[str]:
    """Every zone-inappropriate citation in ``case[field]``, fully described.

    Each line names the case, the field, the offending token, the case's zone
    and the zones the provision governs (acceptance criterion 4).
    """
    zone = case.get("zone")
    values = case.get(field) or []
    lines: list[str] = []
    for citation in citations_in(values):
        reason = zone_violation(zone, citation, chapter_map)
        if reason:
            lines.append(f"{case['id']} ({field}, zone {zone}): {reason}")
    return lines


def all_violations(
    cases: Iterable[dict[str, Any]], fields: Iterable[str], chapter_map: dict[str, Any]
) -> list[str]:
    return [
        line
        for case in cases
        for field in fields
        for line in case_violations(case, field, chapter_map)
    ]
