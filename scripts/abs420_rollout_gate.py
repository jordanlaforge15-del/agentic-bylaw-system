"""Gate the retrieval_enabled rollout against the corpus it was reasoned about (ABS-420).

Migration ``0024_document_retrieval_enabled`` replaces the recency-derived
retrieval scope with an explicit per-document flag, and backfills that flag
with exactly what ``latest_per_bylaw_resolver`` would have selected — newest
ingest per ``(municipality, bylaw_name)``, ties broken by highest id. The
rollout is therefore behaviour-preserving *if and only if* production's
document table is still the table that claim was measured against.

This module is the mechanical form of that "if and only if". It reads a
``psql`` dump of the document table, predicts the backfill winners with the
migration's own ordering rule, and compares both against the state the
rollout was planned for:

  preflight  — before the deploy: the inventory must match, so the operator
               knows the enabled set the migration is about to produce.
  verify     — after the deploy: the flags on disk must equal that prediction,
               so a backfill that ran against a moved corpus is caught while
               the maintenance window is still open.

Everything here is pure: inventory in, verdict out, no database and no SSH.
The procedure around it lives in ``scripts/apply-abs420-retrieval-rollout.sh``
and the tests in ``tests/test_abs420_rollout_gate.py`` drive this file
directly.

Usage::

    # feed it `psql -At -F'|' -c "<INVENTORY_SQL>"` output
    python scripts/abs420_rollout_gate.py --preflight < inventory.txt
    python scripts/abs420_rollout_gate.py --verify < inventory.txt
    python scripts/abs420_rollout_gate.py --print-sql
"""
from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass

# The columns the gate reads, in order, pipe-separated. `retrieval_enabled`
# does not exist before migration 0024, so preflight asks for five columns and
# verify for six — one SQL string each, both emitted by --print-sql.
INVENTORY_SQL = (
    "SELECT id, municipality, bylaw_name, ingestion_timestamp, parser_version "
    "FROM document ORDER BY id"
)
INVENTORY_SQL_WITH_FLAG = (
    "SELECT id, municipality, bylaw_name, ingestion_timestamp, parser_version, "
    "retrieval_enabled FROM document ORDER BY id"
)


@dataclass(frozen=True)
class DocumentRow:
    id: int
    municipality: str
    bylaw_name: str
    ingestion_timestamp: str
    parser_version: str
    retrieval_enabled: bool | None = None


# Production's document table as measured on 2026-08-27, over an SSH read of
# the bylaw-postgres container. The rollout's behaviour-preservation claim is
# a claim about exactly these four rows; if prod no longer looks like this,
# the claim has not been checked and the gate stops the run.
#
# id 1 and id 2 are the same by-law ingested six minutes apart: 1 by the
# pymupdf fallback parser, 2 by docling. The backfill's newest-wins rule
# picks 2, which is also the better ingest — recency and quality agree here,
# so no curation is required. That agreement is a fact about this corpus, not
# a property of the rule, which is why it is asserted rather than assumed.
EXPECTED_INVENTORY: tuple[DocumentRow, ...] = (
    DocumentRow(1, "HRM", "Halifax Peninsula Land Use Bylaw", "2026-04-29 00:12:49.286132+00", "pymupdf-fallback"),
    DocumentRow(2, "HRM", "Halifax Peninsula Land Use Bylaw", "2026-04-29 00:18:08.441475+00", "docling"),
    DocumentRow(4, "HRM", "Regional Centre Land Use By-Law", "2026-05-03 19:26:21.643371+00", "docling:halifax"),
    DocumentRow(5, "HRM", "Halifax Mainland Land Use By-law", "2026-05-23 23:45:55.260534+00", "docling:manifest:hrm-mainland"),
)

# The set an operator intends to serve retrieval from after the rollout: one
# document per real by-law, each the better of the ingests available. Held
# separately from the prediction on purpose — the backfill's output being
# right is the thing being checked, not the definition of right.
INTENDED_ENABLED_IDS: frozenset[int] = frozenset({2, 4, 5})


def parse_inventory(text: str) -> list[DocumentRow]:
    """Parse ``psql -At -F'|'`` output into rows, tolerating the trailing count.

    ``psql`` without ``-t`` appends a "(4 rows)" line; ``-At`` does not, but
    an operator pasting from an interactive session might include it. Blank
    lines and that footer are skipped rather than parsed into a bad verdict.
    """
    rows: list[DocumentRow] = []
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("(") or line.startswith("-"):
            continue
        parts = line.split("|")
        if len(parts) not in (5, 6):
            raise ValueError(
                f"unparseable inventory line (expected 5 or 6 pipe-separated fields): {line!r}"
            )
        enabled: bool | None = None
        if len(parts) == 6:
            flag = parts[5].strip().lower()
            if flag not in ("t", "f", "true", "false"):
                raise ValueError(f"unparseable retrieval_enabled value {parts[5]!r} in: {line!r}")
            enabled = flag in ("t", "true")
        rows.append(
            DocumentRow(
                id=int(parts[0]),
                municipality=parts[1].strip(),
                bylaw_name=parts[2].strip(),
                ingestion_timestamp=parts[3].strip(),
                parser_version=parts[4].strip(),
                retrieval_enabled=enabled,
            )
        )
    return rows


def predict_backfill_winners(rows: list[DocumentRow]) -> set[int]:
    """The ids migration 0024's backfill will enable.

    Mirrors the migration's window function exactly — PARTITION BY
    (municipality, bylaw_name), ORDER BY ingestion_timestamp DESC, id DESC,
    take rn = 1 — which in turn mirrors the pre-ABS-413
    ``latest_per_bylaw_resolver`` it replaces. ``tests/test_abs420_rollout_
    gate.py`` executes the migration's real SQL and compares its winners with
    this function's, so the two cannot drift.

    Timestamps are compared as strings. Postgres renders them in a fixed,
    zero-padded ISO layout at a single UTC offset here, so lexicographic and
    chronological order coincide; a mixed-offset dump would not parse into
    this gate's expected inventory in the first place.
    """
    winners: dict[tuple[str, str], DocumentRow] = {}
    for row in rows:
        key = (row.municipality, row.bylaw_name)
        current = winners.get(key)
        if current is None or (row.ingestion_timestamp, row.id) > (
            current.ingestion_timestamp,
            current.id,
        ):
            winners[key] = row
    return {row.id for row in winners.values()}


def _inventory_drift(rows: list[DocumentRow]) -> list[str]:
    """Every way the observed inventory differs from the measured one."""
    problems: list[str] = []
    observed = {row.id: row for row in rows}
    expected = {row.id: row for row in EXPECTED_INVENTORY}

    for extra in sorted(set(observed) - set(expected)):
        row = observed[extra]
        problems.append(
            f"document {extra} ({row.municipality} / {row.bylaw_name}) was ingested "
            "after the rollout was planned"
        )
    for gone in sorted(set(expected) - set(observed)):
        row = expected[gone]
        problems.append(f"document {gone} ({row.bylaw_name}) is no longer in the corpus")
    for doc_id in sorted(set(observed) & set(expected)):
        seen, want = observed[doc_id], expected[doc_id]
        for field in ("municipality", "bylaw_name", "ingestion_timestamp", "parser_version"):
            if getattr(seen, field) != getattr(want, field):
                problems.append(
                    f"document {doc_id} {field}: expected {getattr(want, field)!r}, "
                    f"found {getattr(seen, field)!r}"
                )
    return problems


def gate_preflight(text: str) -> tuple[int, list[str]]:
    """Pre-deploy: does production still look like the corpus we planned against?"""
    rows = parse_inventory(text)
    lines: list[str] = []
    problems = _inventory_drift(rows)
    if problems:
        lines.append("STOP: production's document table no longer matches the 2026-08-27 measurement.")
        lines.extend(f"  - {problem}" for problem in problems)
        lines.append(
            "  The behaviour-preservation claim was reasoned about against that table. "
            "Re-measure and re-plan; do not run the rollout on this corpus."
        )
        return 1, lines

    predicted = predict_backfill_winners(rows)
    lines.append("Inventory matches the 2026-08-27 measurement (4 documents).")
    lines.append(f"Migration 0024 will enable: {sorted(predicted)}")
    lines.append(f"Migration 0024 will leave disabled: {sorted(set(r.id for r in rows) - predicted)}")
    if predicted != set(INTENDED_ENABLED_IDS):
        lines.append(
            f"STOP: the backfill would enable {sorted(predicted)}, but the intended "
            f"retrieval corpus is {sorted(INTENDED_ENABLED_IDS)}. Curation is required "
            "after the migration — run --verify, then enable/disable to the intended set."
        )
        return 1, lines
    lines.append("Predicted enabled set equals the intended corpus — no post-migration curation needed.")
    return 0, lines


def gate_verify(text: str) -> tuple[int, list[str]]:
    """Post-deploy: are the flags on disk the ones the plan predicted?"""
    rows = parse_inventory(text)
    lines: list[str] = []
    missing_flag = [row.id for row in rows if row.retrieval_enabled is None]
    if missing_flag or not rows:
        lines.append(
            "STOP: the dump carries no retrieval_enabled column — migration 0024 has "
            "not run against this database, or the wrong query was used."
        )
        return 1, lines

    problems = _inventory_drift(rows)
    if problems:
        lines.append("STOP: the corpus moved between the preflight and this verification.")
        lines.extend(f"  - {problem}" for problem in problems)
        return 1, lines

    enabled = {row.id for row in rows if row.retrieval_enabled}
    lines.append(f"Enabled after the migration: {sorted(enabled)}")
    if enabled != set(INTENDED_ENABLED_IDS):
        surplus = sorted(enabled - INTENDED_ENABLED_IDS)
        shortfall = sorted(INTENDED_ENABLED_IDS - enabled)
        lines.append(
            f"STOP: enabled set is not the intended corpus {sorted(INTENDED_ENABLED_IDS)}."
        )
        if surplus:
            lines.append(f"  - disable: {surplus}  (layer1 disable-retrieval {' '.join(map(str, surplus))})")
        if shortfall:
            lines.append(f"  - enable:  {shortfall}  (layer1 enable-retrieval {' '.join(map(str, shortfall))})")
        return 1, lines

    # No normalized-identity check here on purpose. Reaching this line means
    # the inventory matched the four measured rows and the enabled ids are
    # exactly {2, 4, 5}, whose names do not normalize equal — so a collision
    # is unreachable, and a check for it would be dead code masquerading as
    # rigour. The live tripwire for a later curation slip is the
    # enabled_name_collisions block of /v1/monitoring/corpus-coherence
    # (ABS-434), which the procedure reads as its last step.
    lines.append(
        f"Enabled set equals the intended corpus {sorted(INTENDED_ENABLED_IDS)}, "
        "one document per by-law. Rollout verified."
    )
    return 0, lines


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--preflight", action="store_true", help="Gate a pre-deploy inventory dump")
    mode.add_argument("--verify", action="store_true", help="Gate a post-deploy inventory dump")
    mode.add_argument("--print-sql", action="store_true", help="Print the two inventory queries")
    args = parser.parse_args(argv)

    if args.print_sql:
        print(f"preflight|{INVENTORY_SQL}")
        print(f"verify|{INVENTORY_SQL_WITH_FLAG}")
        return 0

    text = sys.stdin.read()
    try:
        code, lines = gate_preflight(text) if args.preflight else gate_verify(text)
    except ValueError as exc:
        print(f"STOP: {exc}", file=sys.stderr)
        return 1
    for line in lines:
        print(line, file=sys.stderr)
    return code


if __name__ == "__main__":
    raise SystemExit(main())
