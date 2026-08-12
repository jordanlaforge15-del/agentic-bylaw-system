#!/usr/bin/env python3
"""
ABS-463: resolve every ``expected_bylaw_references`` entry in
``evals/regional_centre_test_prompts.json`` against the real Halifax Regional
Centre Land Use By-law ingest, and snapshot the result to
``evals/regional_centre_bylaw_reference_index.json``.

Why a snapshot instead of a live query in the test?
------------------------------------------------------
``tests/test_eval_bylaw_references.py`` has to run in CI and in every worktree,
where the only database is the seeded e2e fixture — it does not contain the
4,300-fragment Halifax ingest. The snapshot is the version-controlled record of
what each reference resolved to *at the time a human verified it*, so the test
can assert offline and a reviewer can re-check a claim by reading one file.

Re-run this script after every re-ingest of the Regional Centre by-law:

    python scripts/build_bylaw_reference_index.py                 # rewrite snapshot
    python scripts/build_bylaw_reference_index.py --check         # CI/preflight: no writes
    python scripts/build_bylaw_reference_index.py --db-url ...    # non-default DB

``--check`` exits non-zero if any reference fails to resolve against the live
corpus, if the committed snapshot's per-reference resolutions have drifted from
what the corpus now says, if the snapshot's provenance (``document_id``,
``source_fragment_count``, ``reference_count``) no longer describes the corpus it
was generated from, or — ABS-471 — if a case cites a provision from a *different
zone's* chapter. ``tests/test_bylaw_reference_index_check.py`` runs this mode
automatically wherever the real ingest is reachable, and skips where it is not.

Resolution was never enough on its own. Every reference defect ABS-470 fixed
resolved perfectly: ``Section 200`` is a real provision, it just governs HR-2
and HR-1 rather than the CDD-2 case citing it. The zone-appropriateness rule
lives in ``scripts/eval_zone_chapters.py`` and reads the chapter boundaries
``scripts/build_zone_chapter_map.py`` derives from this same corpus.

Reference grammar (the only forms allowed in the eval file)
-----------------------------------------------------------
    Section 198            an operative section
    Section 9(1)(c)        a subsection/clause under a section
    Table 1A               a numbered table
    Schedule 15            a schedule that exists as a fragment in the ingest
    Appendix 2             an appendix

Anything else is a hard error: it keeps the eval file from accumulating
free-text "references" that no grader can check.
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
PROMPTS_FILE = REPO_ROOT / "evals" / "regional_centre_test_prompts.json"
INDEX_FILE = REPO_ROOT / "evals" / "regional_centre_bylaw_reference_index.json"

BYLAW_NAME = "Regional Centre Land Use By-Law"
DEFAULT_DB_URL = "postgresql+psycopg://layer1:layer1@localhost:5432/layer1"

EXCERPT_CHARS = 240

SECTION_RE = re.compile(r"^Section (\d+[A-Z]?)((?:\(\w+\))*)$")
TABLE_RE = re.compile(r"^Table (\d+[A-Z]?)$")
SCHEDULE_RE = re.compile(r"^Schedule (\d+[A-Z]?)$")
APPENDIX_RE = re.compile(r"^Appendix (\d+)$")
GROUP_RE = re.compile(r"\((\w+)\)")


class UnparseableReference(ValueError):
    """The reference string does not match the allowed grammar."""


def normalise(text: str | None) -> str:
    return " ".join((text or "").split())


# ---------------------------------------------------------------------------
# Resolution
# ---------------------------------------------------------------------------


def resolution_plan(reference: str) -> dict[str, Any]:
    """Translate a reference string into the lookup that resolves it.

    Returned dict is recorded verbatim in the snapshot so a reader can re-run
    the same lookup by hand.
    """
    match = SECTION_RE.match(reference)
    if match:
        section, groups = match.group(1), GROUP_RE.findall(match.group(2))
        # The by-law's operative sections and the internal sections of its
        # appendices share the 1..N numbering namespace ("Section 9" is both a
        # development-permit exemption and a shadow-diagram rule in Appendix 2).
        # A bare "Section N" citation always means the operative one, so exclude
        # appendix-parented fragments. Sections mis-parented onto a Schedule by
        # the ingest (e.g. "Schedule 17 > 111") still resolve.
        not_appendix = "AND citation_path NOT LIKE 'Appendix %'"
        if not groups:
            return {
                "kind": "section",
                "section": section,
                "sql": (
                    "SELECT id, citation_label, citation_path, page_start, text "
                    "FROM source_fragment WHERE document_id = :doc "
                    "AND fragment_type = 'SECTION' AND citation_label = :label "
                    f"AND citation_path IS NOT NULL {not_appendix}"
                ),
                "params": {"label": section},
            }
        # Address the innermost group; the section number disambiguates it,
        # because clause labels like "(c)" repeat throughout the by-law.
        target = f"({groups[-1]})"
        return {
            "kind": "section_clause",
            "section": section,
            "clause": target,
            "sql": (
                "SELECT id, citation_label, citation_path, page_start, text "
                "FROM source_fragment WHERE document_id = :doc "
                "AND citation_label = :label AND citation_path IS NOT NULL "
                f"AND citation_path LIKE :path_like {not_appendix}"
            ),
            "params": {"label": target, "path_like": f"%> {section} >%"},
        }

    for regex, kind, prefix, frag_types in (
        (TABLE_RE, "table", "Table", None),
        (SCHEDULE_RE, "schedule", "Schedule", None),
        (APPENDIX_RE, "appendix", "Appendix", ("APPENDIX",)),
    ):
        match = regex.match(reference)
        if not match:
            continue
        plan: dict[str, Any] = {
            "kind": kind,
            "sql": (
                "SELECT id, citation_label, citation_path, page_start, text "
                "FROM source_fragment WHERE document_id = :doc "
                "AND citation_label = :label AND citation_path IS NOT NULL"
            ),
            "params": {"label": f"{prefix} {match.group(1)}"},
        }
        if frag_types:
            plan["sql"] += " AND fragment_type = ANY(:frag_types)"
            plan["params"]["frag_types"] = list(frag_types)
        return plan

    raise UnparseableReference(
        f"{reference!r} does not match the allowed grammar "
        "(Section N, Section N(a)(b), Table N, Schedule N, Appendix N)"
    )


def resolve(conn, document_id: int, reference: str) -> dict[str, Any]:
    import sqlalchemy as sa

    plan = resolution_plan(reference)
    params = dict(plan["params"], doc=document_id)
    rows = conn.execute(sa.text(plan["sql"]), params).fetchall()

    matches = [
        {
            "fragment_id": row[0],
            "citation_label": row[1],
            "citation_path": row[2],
            "page": row[3],
            "excerpt": normalise(row[4])[:EXCERPT_CHARS],
        }
        for row in sorted(rows, key=lambda r: r[0])
    ]
    return {
        "reference": reference,
        "kind": plan["kind"],
        "resolved": bool(matches),
        "ambiguous": len(matches) > 1,
        "verified_by": {
            "sql": plan["sql"],
            "params": {k: v for k, v in plan["params"].items()},
        },
        "matches": matches,
    }


# ---------------------------------------------------------------------------
# Snapshot
# ---------------------------------------------------------------------------


def build_index(db_url: str) -> dict[str, Any]:
    import sqlalchemy as sa

    cases = json.loads(PROMPTS_FILE.read_text())
    references = sorted(
        {ref for case in cases for ref in case.get("expected_bylaw_references", [])}
    )

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
        entries = {ref: resolve(conn, document_id, ref) for ref in references}
        fragment_count = conn.execute(
            sa.text("SELECT count(*) FROM source_fragment WHERE document_id = :doc"),
            {"doc": document_id},
        ).scalar()

    return {
        "_comment": (
            "Generated by scripts/build_bylaw_reference_index.py (ABS-463). "
            "Every entry records the SQL that resolved the reference against the "
            "Regional Centre ingest, so it can be re-checked without redoing the "
            "research. Re-run the script after any re-ingest."
        ),
        "bylaw_name": BYLAW_NAME,
        "document_id": document_id,
        "source_fragment_count": fragment_count,
        "reference_count": len(entries),
        "references": entries,
    }


def unresolved(index: dict[str, Any]) -> list[str]:
    return sorted(ref for ref, e in index["references"].items() if not e["resolved"])


def _comparable(index: dict[str, Any]) -> Any:
    """Strip per-match fields that legitimately move between ingests (row ids).

    Provenance (``document_id``, ``source_fragment_count``, ``reference_count``)
    is deliberately *not* compared here — it is not a per-reference property.
    :func:`provenance_drift` owns that comparison.
    """
    return {
        ref: {
            "kind": entry["kind"],
            "resolved": entry["resolved"],
            "paths": sorted(m["citation_path"] for m in entry["matches"]),
        }
        for ref, entry in index["references"].items()
    }


# Snapshot fields that record *which corpus the snapshot came from*, mapped to
# the phrasing used when one of them no longer matches the live database.
PROVENANCE_FIELDS = {
    "document_id": "document row the snapshot was built from",
    "source_fragment_count": "fragments in the corpus",
    "reference_count": "references in the snapshot",
}


def zone_appropriateness_failures(prompts_file: Path = PROMPTS_FILE) -> list[str]:
    """Cases citing a provision from another zone's chapter (ABS-471, G3).

    Pure and database-free: the chapter boundaries come from the committed
    ``regional_centre_zone_chapter_map.json`` snapshot, which
    ``build_zone_chapter_map.py --check`` keeps honest against the corpus. That
    split is what lets this run inside ``--check`` without a second set of
    queries, and inside pytest without a database at all.
    """
    try:  # importable either as `scripts.x` (pytest) or as `x` (./scripts/foo.py)
        from scripts.build_zone_chapter_map import load_map
        from scripts.eval_zone_chapters import all_violations
    except ImportError:  # pragma: no cover - depends on how the caller set sys.path
        from build_zone_chapter_map import load_map
        from eval_zone_chapters import all_violations

    cases = json.loads(prompts_file.read_text())
    return all_violations(cases, ("expected_bylaw_references",), load_map())


def provenance_drift(committed: dict[str, Any], live: dict[str, Any]) -> list[str]:
    """Compare a committed snapshot's provenance against a freshly built one.

    Pure — takes two index dicts, touches no database — so the guard itself can
    be tested offline (ABS-464 DoD #5). Returns one human-readable line per
    field that has drifted, naming both numbers; an empty list means the
    snapshot describes the corpus it is being checked against.
    """
    drift: list[str] = []
    for field, description in PROVENANCE_FIELDS.items():
        expected = live.get(field)
        actual = committed.get(field, "<absent>")
        if actual != expected:
            drift.append(
                f"{field}: snapshot records {actual}, live corpus has {expected} "
                f"({description})"
            )
    return drift


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--db-url",
        default=os.environ.get("BYLAW_REFERENCE_DB_URL", DEFAULT_DB_URL),
        help=f"SQLAlchemy URL for the layer1 database (default: {DEFAULT_DB_URL})",
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="Verify against the live corpus without writing; exit 1 on drift.",
    )
    args = parser.parse_args(argv)

    index = build_index(args.db_url)

    missing = unresolved(index)
    if missing:
        print("UNRESOLVED references (they do not exist in the corpus):", file=sys.stderr)
        for ref in missing:
            print(f"  - {ref}", file=sys.stderr)
        return 1

    ambiguous = sorted(ref for ref, e in index["references"].items() if e["ambiguous"])
    if ambiguous:
        print(
            "NOTE: these references match more than one fragment "
            "(label collisions between Parts and Appendices): "
            + ", ".join(ambiguous)
        )

    # ABS-471 (G3). Reported in both modes because a regeneration that silently
    # re-snapshots a wrong-zone citation is how the eval file rotted the first
    # time; only --check treats it as fatal, so the snapshot can still be
    # rebuilt while the citations are being corrected.
    misfiled = zone_appropriateness_failures()
    if misfiled:
        print(
            "ZONE-INAPPROPRIATE references (they resolve, but they govern a "
            "different zone):",
            file=sys.stderr,
        )
        for line in misfiled:
            print(f"  - {line}", file=sys.stderr)

    if args.check:
        if misfiled:
            return 1
        if not INDEX_FILE.exists():
            print(f"{INDEX_FILE} is missing; run without --check.", file=sys.stderr)
            return 1
        committed = json.loads(INDEX_FILE.read_text())
        if _comparable(committed) != _comparable(index):
            print(
                f"{INDEX_FILE.name} is stale relative to the corpus. "
                "Re-run without --check and review the diff.",
                file=sys.stderr,
            )
            return 1
        drift = provenance_drift(committed, index)
        if drift:
            print(
                f"{INDEX_FILE.name} no longer describes the corpus it is being "
                "checked against. Every reference still resolves, but the "
                "snapshot's provenance has drifted:",
                file=sys.stderr,
            )
            for line in drift:
                print(f"  - {line}", file=sys.stderr)
            print(
                "Re-run without --check to regenerate the snapshot against the "
                "current corpus, then review the diff.",
                file=sys.stderr,
            )
            return 1
        print(
            f"OK: {index['reference_count']} references resolve against "
            f"document_id={index['document_id']}, each belongs to a chapter "
            f"governing its case's zone, and the snapshot's provenance matches "
            f"the live corpus ({index['source_fragment_count']} fragments)."
        )
        return 0

    INDEX_FILE.write_text(json.dumps(index, indent=2, ensure_ascii=False) + "\n")
    print(
        f"Wrote {INDEX_FILE.relative_to(REPO_ROOT)} "
        f"({index['reference_count']} references, "
        f"document_id={index['document_id']}, "
        f"{index['source_fragment_count']} fragments in the corpus)."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
