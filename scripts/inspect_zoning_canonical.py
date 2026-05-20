"""Read back the e2e zoning fixture's canonical attributes as JSON.

The ABS-66 Playwright spec uses this to assert that the ingest path
resolved the upstream BYLAW_ID integer into a publisher-prefixed code
and a human-readable bylaw name. We surface the raw
``canonical_attributes_json`` so the spec can pin every field that
matters without expanding the public API.

Usage::

    .venv/bin/python scripts/inspect_zoning_canonical.py \\
        --globalid e2e-zoning-abs66-mainland
"""
from __future__ import annotations

import argparse
import json
import sys

from sqlalchemy import select

from layer1.db.base import ExternalDatasetFeature
from layer1.db.session import session_scope


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--globalid", required=True, help="feature_key to look up")
    args = parser.parse_args()

    with session_scope() as db:
        feature = db.scalar(
            select(ExternalDatasetFeature).where(
                ExternalDatasetFeature.feature_key == args.globalid
            )
        )
        if feature is None:
            print(json.dumps({"error": "feature not found", "feature_key": args.globalid}))
            return 1
        print(json.dumps(feature.canonical_attributes_json or {}, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
