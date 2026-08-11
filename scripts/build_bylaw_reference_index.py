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
corpus, or if the committed snapshot has drifted from what the corpus now says.

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
    """Strip fields that legitimately drift between ingests (row ids, counts)."""
    return {
        ref: {
            "kind": entry["kind"],
            "resolved": entry["resolved"],
            "paths": sorted(m["citation_path"] for m in entry["matches"]),
        }
        for ref, entry in index["references"].items()
    }


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

    if args.check:
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
        print(f"OK: {index['reference_count']} references resolve; snapshot is current.")
        return 0

    INDEX_FILE.write_text(json.dumps(index, indent=2, ensure_ascii=False) + "\n")
    print(f"Wrote {INDEX_FILE.relative_to(REPO_ROOT)} ({index['reference_count']} references).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
