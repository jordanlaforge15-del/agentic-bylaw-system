"""One-shot smoke check for ABS-71: evaluator returns non-uncertain.

Runs `evaluate_submission_against_bylaws` against doc 4 with a plausible
Halifax submission and verifies at least one attribute comes back with
something other than `uncertain`. Intentionally kept tiny: this is the
last "done when" item on the issue, not a piece of production code.

Run from the worktree root with the prod tunnel up:

    DATABASE_URL=postgresql+psycopg://layer1:<pwd>@localhost:5471/layer1 \
        .venv/bin/python scripts/abs71_smoke_check.py
"""
from __future__ import annotations

import json
import os
import sys

from layer1.db.session import session_scope
from layer2.compliance.evaluator import (
    DocumentFilters,
    EvaluationRequest,
    EvaluatorService,
    SubmissionAttributeInput,
)


# A plausible-but-not-real Halifax residential submission. Values are
# inside the realistic envelope for an ER-1 / ER-2 lot, which is the
# kind of context the freshly-tagged setback / height / zone clauses
# should now light up.
SUBMISSION_ATTRIBUTES = [
    SubmissionAttributeInput(attribute_key="zone_code", value="ER-2"),
    SubmissionAttributeInput(attribute_key="building_height_m", value=11.0, unit="m"),
    SubmissionAttributeInput(attribute_key="front_setback_m", value=4.5, unit="m"),
    SubmissionAttributeInput(attribute_key="rear_setback_m", value=6.0, unit="m"),
    SubmissionAttributeInput(attribute_key="side_setback_left_m", value=1.5, unit="m"),
    SubmissionAttributeInput(attribute_key="side_setback_right_m", value=1.5, unit="m"),
    SubmissionAttributeInput(attribute_key="lot_area_m2", value=500.0, unit="m2"),
    SubmissionAttributeInput(attribute_key="residential_unit_count", value=2),
]


def main() -> int:
    with session_scope(os.environ.get("DATABASE_URL")) as session:
        service = EvaluatorService(session)
        request = EvaluationRequest(
            attributes=SUBMISSION_ATTRIBUTES,
            document_filters=DocumentFilters(document_id=4),
            persist_decision=False,
        )
        response = service.evaluate(request)

    payload = response.to_json()
    # Trim citations for terminal readability — full payload still
    # available via json.dumps on `payload`.
    summary = {
        "overall_status": payload["overall_status"],
        "evaluator_version": payload["evaluator_version"],
        "taxonomy_version": payload["taxonomy_version"],
        "attribute_verdicts": [
            {
                "attribute_key": r["attribute_key"],
                "submitted_value": r["submitted_value"],
                "verdict": r["verdict"],
                "delta": r["delta"],
                "applicable_clause_count": len(r["applicable_clauses"]),
                "first_citation": (
                    r["applicable_clauses"][0]["citation_path"]
                    if r["applicable_clauses"]
                    else None
                ),
            }
            for r in payload["attribute_results"]
        ],
        "unevaluated_attributes": payload["unevaluated_attributes"],
    }
    print(json.dumps(summary, indent=2, default=str))

    non_uncertain = [
        a for a in summary["attribute_verdicts"] if a["verdict"] != "uncertain"
    ]
    if not non_uncertain:
        print(
            "FAIL: every attribute came back uncertain — tagging is not "
            "feeding the evaluator.",
            file=sys.stderr,
        )
        return 1
    print(
        f"OK: {len(non_uncertain)} attribute(s) returned a non-uncertain verdict; "
        "first three: "
        + ", ".join(
            f"{a['attribute_key']}={a['verdict']}" for a in non_uncertain[:3]
        )
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
