"""How much real work was built on a weak address resolution? (ABS-466)

The geocoder used to accept GEOMETRIC_CENTER matches for civic addresses,
and nothing downstream reported the quality of a resolution, so a zone
picked from an estimated point was indistinguishable from one picked from
a rooftop match. That fix stops NEW weak resolutions from presenting as
facts. This script answers the other half of the question: how many are
already in the database, and how much user-facing work sits on top of them.

It reports, for whichever database ``DATABASE_URL`` points at:

  1. Every linked ``geocode_cache`` row bucketed by resolution quality
     (rooftop / interpolated / centroid / approximate / unknown), using the
     same classifier the runtime uses.
  2. The below-rooftop addresses themselves, so they can be re-geocoded or
     confirmed by hand.
  3. ``answer_log`` rows whose question text names one of those addresses.
  4. Advisor cases (``advisor_case.anchor_label``) anchored on one, when the
     advisor schema is present — a case is the unit a real user would need
     to be notified about.

Read-only: it issues SELECTs and nothing else.

Usage::

    DATABASE_URL=postgresql+psycopg://user:pw@host:5432/db \\
        .venv/bin/python scripts/geocode_confidence_exposure.py [--json]
"""
from __future__ import annotations

import json
import os
import sys
from collections import Counter, defaultdict
from typing import Any

from sqlalchemy import create_engine, inspect, text

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from layer2.retrieval.resolution_quality import classify_resolution  # noqa: E402


def _rows(conn, sql: str, **params) -> list[dict[str, Any]]:
    return [dict(r) for r in conn.execute(text(sql), params).mappings()]


def collect(database_url: str) -> dict[str, Any]:
    engine = create_engine(database_url)
    tables = set(inspect(engine).get_table_names())
    report: dict[str, Any] = {"database": engine.url.render_as_string(hide_password=True)}

    if "geocode_cache" not in tables:
        report["error"] = "no geocode_cache table in this database"
        return report

    with engine.connect() as conn:
        cache = _rows(
            conn,
            """
            SELECT id, raw_text, kind, status, resolver, confidence, metadata_json
            FROM geocode_cache
            ORDER BY id
            """,
        )

        buckets: Counter[str] = Counter()
        weak: list[dict[str, Any]] = []
        for row in cache:
            if row["status"] != "linked":
                buckets["not_linked"] += 1
                continue
            metadata = row["metadata_json"] or {}
            if isinstance(metadata, str):
                metadata = json.loads(metadata)
            quality = classify_resolution(
                metadata.get("location_type"), row["confidence"]
            )
            buckets[quality] += 1
            if quality != "rooftop":
                weak.append(
                    {
                        "raw_text": row["raw_text"],
                        "kind": row["kind"],
                        "resolver": row["resolver"],
                        "confidence": row["confidence"],
                        "location_type": metadata.get("location_type"),
                        "quality": quality,
                    }
                )

        report["geocode_cache_total"] = len(cache)
        report["geocode_cache_by_quality"] = dict(sorted(buckets.items()))
        report["below_rooftop_addresses"] = weak

        # An address is only exposure if something user-facing was built on
        # it. Match on the address text: the extractor pulled it out of that
        # same text on the way in, so a substring match is the honest join.
        addresses = [w["raw_text"] for w in weak if w["raw_text"]]

        answer_hits: dict[str, int] = defaultdict(int)
        if addresses and {"answer_log", "query_session"} <= tables:
            for address in addresses:
                hit = conn.execute(
                    text(
                        """
                        SELECT count(*) FROM answer_log a
                        JOIN query_session q ON q.id = a.query_session_id
                        WHERE lower(q.question_text) LIKE lower(:pattern)
                        """
                    ),
                    {"pattern": f"%{address}%"},
                ).scalar_one()
                if hit:
                    answer_hits[address] = int(hit)
            report["answer_log_rows_on_weak_addresses"] = sum(answer_hits.values())
            report["answer_log_by_address"] = dict(answer_hits)
        else:
            report["answer_log_rows_on_weak_addresses"] = (
                None if "answer_log" not in tables else 0
            )

        case_hits: dict[str, int] = defaultdict(int)
        if addresses and "advisor_case" in tables:
            for address in addresses:
                hit = conn.execute(
                    text(
                        """
                        SELECT count(*) FROM advisor_case
                        WHERE lower(anchor_label) LIKE lower(:pattern)
                        """
                    ),
                    {"pattern": f"%{address}%"},
                ).scalar_one()
                if hit:
                    case_hits[address] = int(hit)
            report["advisor_cases_on_weak_addresses"] = sum(case_hits.values())
            report["advisor_cases_by_address"] = dict(case_hits)
        else:
            report["advisor_cases_on_weak_addresses"] = (
                None if "advisor_case" not in tables else 0
            )

    return report


def _print_human(report: dict[str, Any]) -> None:
    print(f"database: {report['database']}")
    if "error" in report:
        print(f"  ERROR: {report['error']}")
        return
    print(f"geocode_cache rows: {report['geocode_cache_total']}")
    for quality, count in report["geocode_cache_by_quality"].items():
        print(f"  {quality:<12} {count}")
    weak = report["below_rooftop_addresses"]
    print(f"below-rooftop linked addresses: {len(weak)}")
    for row in weak:
        print(
            f"  {row['quality']:<12} conf={row['confidence']} "
            f"type={row['location_type']} resolver={row['resolver']} "
            f"{row['raw_text']!r}"
        )
    print(
        "answer_log rows built on one: "
        f"{report['answer_log_rows_on_weak_addresses']}"
    )
    for address, count in (report.get("answer_log_by_address") or {}).items():
        print(f"  {count:>4}  {address}")
    print(f"advisor cases anchored on one: {report['advisor_cases_on_weak_addresses']}")
    for address, count in (report.get("advisor_cases_by_address") or {}).items():
        print(f"  {count:>4}  {address}")


def main(argv: list[str]) -> int:
    database_url = os.environ.get("DATABASE_URL")
    if not database_url:
        print("DATABASE_URL is not set", file=sys.stderr)
        return 2
    report = collect(database_url)
    if "--json" in argv:
        print(json.dumps(report, indent=2, default=str))
    else:
        _print_human(report)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
