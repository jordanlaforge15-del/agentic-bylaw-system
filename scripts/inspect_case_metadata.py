"""Print ``advisor_case.metadata_json`` for a given case_id as JSON.

Test scaffolding for the lot-facts Playwright spec — ``CaseOut`` does
not expose ``metadata_json`` over the HTTP API (it's an internal
chat-layer detail today), so to assert on ``spatial_facts.frontage_m``
etc. the spec invokes this script via ``execSync`` after opening a
case. Output is a single JSON object on stdout so the test can
``JSON.parse`` it directly.

Usage::

    DATABASE_URL=postgresql+psycopg://layer1:layer1@localhost:5432/layer1_test \
        .venv/bin/python scripts/inspect_case_metadata.py --case-id 42

Exits non-zero with a human-readable stderr message when the case isn't
found, so a typo or stale case_id in the spec fails the test cleanly.
"""
from __future__ import annotations

import argparse
import json
import sys

from advisor.db.models import Case
from layer1.db.session import session_scope


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--case-id", type=int, required=True)
    args = parser.parse_args()

    with session_scope() as db:
        case = db.get(Case, args.case_id)
        if case is None:
            print(f"case_id={args.case_id} not found", file=sys.stderr)
            return 1
        # ``metadata_json`` is mutable-dict-backed; coerce to a plain dict
        # so json.dumps doesn't trip on the SQLAlchemy wrapper.
        payload = dict(case.metadata_json or {})
        print(json.dumps(payload))
    return 0


if __name__ == "__main__":
    sys.exit(main())
