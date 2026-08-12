#!/usr/bin/env python3
"""ABS-471: derive the by-law's zone chapters from the corpus, and snapshot them.

The Regional Centre by-law is chaptered by zone: Part V, Chapter 7 carries the
built-form rules for HR-2 and HR-1, Chapter 2 those for DD, Part VI, Chapter 2
those for HCD-SV. A section only governs its own chapter's zones, so
``Section 200`` (HR) cited on an INS case is wrong no matter how cleanly it
resolves — which is exactly the defect ABS-470 cleaned up by hand across four
cases, and ``Section 344`` (Schmidtville HCD) on an ER-3 case besides.

Making that rule checkable needs the chapter boundaries. They are *derived*
here rather than typed in, because the by-law is re-ingested and a hand-typed
range silently stops describing the document it claims to:

* Each chapter heading names itself and the zones it governs — "Part V,
  Chapter 7: Built Form and Siting Requirements within the HR-2 and HR-1
  Zones", "Part VI, Chapter 2: ... for the Schmidtville Heritage Conservation
  District (SHCD) / HCD-SV Zone". A heading that names no zone code (Part V
  Chapter 1 "General Built Form", Chapter 19 "Accessory Structures") governs
  every zone and constrains nothing.
* The sections between one chapter heading and the next are that chapter's.
* The permitted-use tables name their own zones in their captions — "Table 1B:
  Permitted uses by zone (ER-3, ER-2, ER-1, CH-2, and CH-1)".

The result is written to ``evals/regional_centre_zone_chapter_map.json`` so the
rule is usable where the 4,300-fragment ingest is not: pytest in a worktree,
Playwright in CI. ``--check`` re-derives and exits non-zero if the committed
snapshot has drifted — the same shape as
``scripts/build_bylaw_reference_index.py --check`` (ABS-463/464).

    python scripts/build_zone_chapter_map.py            # rewrite snapshot
    python scripts/build_zone_chapter_map.py --check    # CI/preflight: no writes

Consumers: ``scripts/eval_zone_chapters.py`` (the rule engine),
``scripts/build_bylaw_reference_index.py --check``,
``tests/test_zone_chapter_map.py``, ``tests/test_eval_keyword_chapters.py`` and
``web/e2e/functional/abs471-eval-corpus-guards.spec.ts``.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parent.parent
MAP_FILE = REPO_ROOT / "evals" / "regional_centre_zone_chapter_map.json"

BYLAW_NAME = "Regional Centre Land Use By-Law"
DEFAULT_DB_URL = "postgresql+psycopg://layer1:layer1@localhost:5432/layer1"

# "Part V, Chapter 7: Built Form and Siting Requirements within the HR-2 and
# HR-1 Zones". Part I..V arrive as PART fragments and Part VI.. as HEADING
# fragments — an ingest inconsistency, not a structural one — so both types are
# read and this pattern decides what counts.
CHAPTER_HEADING_RE = re.compile(r"^Part ([IVXL]+), Chapter (\d+):\s*(.+)$")

# The zone list follows "within the" / "for the" / "in the" and runs to the
# trailing "Zone"/"Zones". Everything in that span that is not a zone code
# (the Schmidtville district's name, "Waterfront Special Areas") is dropped by
# the tokeniser, so a chapter that governs no particular zone yields [].
CHAPTER_ZONES_RE = re.compile(r"(?:within|for|in) the (.+?) Zones?\b", re.IGNORECASE)

# "Table 1B: Permitted uses by zone (ER-3, ER-2, ER-1, CH-2, and CH-1)".
TABLE_CAPTION_RE = re.compile(r"Permitted uses by zone \(([^)]*)\)", re.IGNORECASE)

# A zone code as the by-law writes it: CEN-1, HR-2, DD, HRI, HCD-SV. The PDF
# extraction inserts stray spaces around hyphens ("CEN- 2", "CH- 2") and drops
# one outright ("ER3"), so tokens are normalised before this is applied.
ZONE_CODE_RE = re.compile(r"^[A-Z]{1,4}(?:-(?:\d|[A-Z]{2}))?$")

_SECTION_LABEL_RE = re.compile(r"^\d+$")


def normalise(text: str | None) -> str:
    return " ".join((text or "").split())


def normalise_zone_token(token: str) -> str | None:
    """Turn one heading token into a zone code, or None if it is not one.

    ``"CEN- 2"`` -> ``"CEN-2"``, ``"ER3"`` -> ``"ER-3"``, ``"and"`` -> None,
    ``"Schmidtville Heritage Conservation District (SHCD)"`` -> None. The
    stray spaces and the missing hyphen are both PDF-extraction artefacts of
    the real corpus, not hypotheticals — see Part V Chapters 5, 9, 10 and 13.
    """
    token = normalise(token).strip(",.").strip()
    if not token:
        return None
    token = re.sub(r"\s*-\s*", "-", token)
    token = re.sub(r"\s+", "", token)
    if token.lower() in {"and", "the"}:
        return None
    # "ER3" -> "ER-3": letters then a single digit with no separator.
    token = re.sub(r"^([A-Z]{2,4})(\d)$", r"\1-\2", token)
    return token if ZONE_CODE_RE.match(token) else None


def zones_in(text: str) -> list[str]:
    """Every zone code named in a comma / "and" / "/"-separated list."""
    zones: list[str] = []
    for token in re.split(r",|\band\b|/", text):
        code = normalise_zone_token(token)
        if code and code not in zones:
            zones.append(code)
    return zones


# ---------------------------------------------------------------------------
# Derivation
# ---------------------------------------------------------------------------


def derive_map(db_url: str) -> dict[str, Any]:
    import sqlalchemy as sa

    engine = sa.create_engine(db_url)
    with engine.connect() as conn:
        document_id = conn.execute(
            sa.text("SELECT id FROM document WHERE bylaw_name = :name ORDER BY id LIMIT 1"),
            {"name": BYLAW_NAME},
        ).scalar()
        if document_id is None:
            raise SystemExit(
                f"No document row for bylaw_name={BYLAW_NAME!r} in {db_url}. "
                "This script needs the real Regional Centre ingest."
            )
        headings = conn.execute(
            sa.text(
                "SELECT id, text FROM source_fragment WHERE document_id = :doc "
                "AND fragment_type IN ('PART', 'HEADING') AND text ~ '^\\s*Part [IVXL]+' "
                "ORDER BY id"
            ),
            {"doc": document_id},
        ).fetchall()
        # Position in the document, not citation_path, decides which chapter a
        # section belongs to. The ingest mis-parents Sections 111-128 — the
        # back half of the DD chapter — onto "Schedule 17 > 111", and Part XVI's
        # sections onto "Part X > ...", so a path-prefix filter would cut the DD
        # chapter off at 110 and let every DD citation from 111 up look like it
        # belonged to no chapter at all. Appendix-parented fragments are excluded
        # for the reason build_bylaw_reference_index.py excludes them: the
        # appendices restart the 1..N numbering and a bare "Section N" never
        # means one of those.
        sections = conn.execute(
            sa.text(
                "SELECT id, citation_label FROM source_fragment "
                "WHERE document_id = :doc AND fragment_type = 'SECTION' "
                "AND (citation_path IS NULL OR citation_path NOT LIKE 'Appendix %') "
                "ORDER BY id"
            ),
            {"doc": document_id},
        ).fetchall()
        tables = conn.execute(
            sa.text(
                "SELECT citation_label, text FROM source_fragment "
                "WHERE document_id = :doc AND citation_label LIKE 'Table 1%' "
                "ORDER BY citation_label"
            ),
            {"doc": document_id},
        ).fetchall()
        fragment_count = conn.execute(
            sa.text("SELECT count(*) FROM source_fragment WHERE document_id = :doc"),
            {"doc": document_id},
        ).scalar()

    heading_ids = [row[0] for row in headings]
    chapters: list[dict[str, Any]] = []
    for position, (heading_id, heading_text) in enumerate(headings):
        heading = CHAPTER_HEADING_RE.match(normalise(heading_text))
        if heading is None:
            continue
        # A chapter runs to the next heading of any Part — Part V's last
        # chapter ends where Part VI begins, not at some Part V sentinel.
        next_id = heading_ids[position + 1] if position + 1 < len(heading_ids) else None
        numbers = sorted(
            {
                int(label)
                for frag_id, label in sections
                if frag_id > heading_id
                and (next_id is None or frag_id < next_id)
                and label
                and _SECTION_LABEL_RE.match(label)
            }
        )
        if not numbers:
            continue
        title = heading.group(3)
        zone_match = CHAPTER_ZONES_RE.search(title)
        chapters.append(
            {
                "part": heading.group(1),
                "chapter": int(heading.group(2)),
                "title": title,
                "first_section": numbers[0],
                "last_section": numbers[-1],
                "zones": zones_in(zone_match.group(1)) if zone_match else [],
                "sections": numbers,
            }
        )

    permitted_use_tables = {}
    for label, text in tables:
        caption = TABLE_CAPTION_RE.search(normalise(text))
        if caption:
            permitted_use_tables[label] = zones_in(caption.group(1))

    return {
        "_comment": (
            "Generated by scripts/build_zone_chapter_map.py (ABS-471). The by-law "
            "is chaptered by zone; a section only governs its own chapter's zones. "
            "Both the chapter boundaries and the permitted-use tables' zone lists "
            "are derived from the ingest — the chapter headings and table captions "
            "name them — so this file describes the corpus rather than someone's "
            "recollection of it. Re-run the script after any re-ingest."
        ),
        "bylaw_name": BYLAW_NAME,
        "document_id": document_id,
        "source_fragment_count": fragment_count,
        "chapters": chapters,
        "permitted_use_tables": permitted_use_tables,
    }


def load_map(path: Path = MAP_FILE) -> dict[str, Any]:
    return json.loads(path.read_text())


def _chapter_key(chapter: dict[str, Any]) -> str:
    return f"Part {chapter.get('part')} Chapter {chapter.get('chapter')}"


def map_drift(committed: dict[str, Any], live: dict[str, Any]) -> list[str]:
    """Human-readable lines for every way the snapshot disagrees with the corpus.

    Pure — two dicts in, strings out — so the drift rule is testable with no
    database, the same split ABS-464 used for the reference index.
    """
    drift: list[str] = []
    for field in ("document_id", "source_fragment_count"):
        if committed.get(field, "<absent>") != live.get(field):
            drift.append(
                f"{field}: snapshot records {committed.get(field, '<absent>')}, "
                f"live corpus has {live.get(field)}"
            )

    was_by_key = {_chapter_key(c): c for c in committed.get("chapters", [])}
    now_by_key = {_chapter_key(c): c for c in live.get("chapters", [])}
    for key in sorted(set(was_by_key) | set(now_by_key)):
        was, now = was_by_key.get(key), now_by_key.get(key)
        if was is None:
            drift.append(f"{key} is in the corpus but not the snapshot")
            continue
        if now is None:
            drift.append(f"{key} is in the snapshot but not the corpus")
            continue
        for field in ("first_section", "last_section", "zones", "title", "sections"):
            if was.get(field) != now.get(field):
                drift.append(
                    f"{key} {field}: snapshot says {was.get(field)!r}, "
                    f"corpus says {now.get(field)!r}"
                )

    committed_tables = committed.get("permitted_use_tables", {})
    live_tables = live.get("permitted_use_tables", {})
    for label in sorted(set(committed_tables) | set(live_tables)):
        if committed_tables.get(label) != live_tables.get(label):
            drift.append(
                f"{label}: snapshot covers {committed_tables.get(label)!r}, "
                f"corpus caption says {live_tables.get(label)!r}"
            )
    return drift


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument(
        "--db-url",
        default=os.environ.get("BYLAW_REFERENCE_DB_URL", DEFAULT_DB_URL),
        help=f"SQLAlchemy URL for the layer1 database (default: {DEFAULT_DB_URL})",
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="Re-derive and compare against the committed snapshot; exit 1 on drift.",
    )
    args = parser.parse_args(argv)

    live = derive_map(args.db_url)

    if args.check:
        if not MAP_FILE.exists():
            print(f"{MAP_FILE} is missing; run without --check.", file=sys.stderr)
            return 1
        drift = map_drift(load_map(), live)
        if drift:
            print(
                f"{MAP_FILE.name} no longer describes the corpus. The by-law's "
                "chapter boundaries have moved:",
                file=sys.stderr,
            )
            for line in drift:
                print(f"  - {line}", file=sys.stderr)
            print(
                "Re-run without --check to regenerate, then review the diff — a "
                "moved boundary changes which sections each zone's cases may cite.",
                file=sys.stderr,
            )
            return 1
        zoned = sum(1 for c in live["chapters"] if c["zones"])
        print(
            f"OK: {len(live['chapters'])} chapters ({zoned} zone-specific) and "
            f"{len(live['permitted_use_tables'])} permitted-use tables match "
            f"document_id={live['document_id']}."
        )
        return 0

    MAP_FILE.write_text(json.dumps(live, indent=2, ensure_ascii=False) + "\n")
    print(
        f"Wrote {MAP_FILE.relative_to(REPO_ROOT)} "
        f"({len(live['chapters'])} chapters, "
        f"{len(live['permitted_use_tables'])} permitted-use tables, "
        f"document_id={live['document_id']})."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
