"""Generate the synthetic IFC fixture the ABS-53 e2e spec uploads.

Writes a small valid IFC4 file to `web/e2e/fixtures/submission-demo.ifc`
so the Playwright spec can post it via the upload form without
depending on a binary blob checked into git.

Idempotent: re-runs cheaply (a couple of seconds) and overwrites in
place. Invoked from `web/e2e/global-setup.ts` alongside the user
seed.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

# Make the test-fixtures package importable without installing the repo.
HERE = Path(__file__).resolve().parent
REPO_ROOT = HERE.parent
sys.path.insert(0, str(REPO_ROOT / "tests"))

from fixtures.submissions.synthetic_ifc import (  # noqa: E402
    SyntheticBuildingSpec,
    SyntheticSpace,
    write_synthetic_ifc,
)


# Halifax NAD83 / UTM 20N base point near the e2e parcel
# (HALIFAX_LON=-63.6, HALIFAX_LAT=44.65 in seed_e2e_evaluator_bylaws.py).
# The exact value doesn't matter for the e2e: the centroid sanity check
# in ABS-52 may still fire because the parcel polygon is generated with
# a degree-tangent-plane approximation rather than a true 2961 grid
# anchor. When it does, the geometric derived attributes skip and the
# pipeline still produces the area-only derived attrs + everything the
# IFC extractor emits, which is what the e2e asserts on.
HALIFAX_BASE_EASTING = 454_500.0
HALIFAX_BASE_NORTHING = 4_946_000.0


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Generate the ABS-53 e2e synthetic IFC fixture."
    )
    parser.add_argument(
        "--out",
        type=Path,
        default=REPO_ROOT / "web" / "e2e" / "fixtures" / "submission-demo.ifc",
        help="Where to write the IFC file.",
    )
    args = parser.parse_args()
    args.out.parent.mkdir(parents=True, exist_ok=True)

    spec = SyntheticBuildingSpec(
        object_type="residential",
        overall_height_m=9.0,
        storey_elevations_m=[0.0, 3.0],
        storey_gross_planned_area_m2=[200.0, 200.0],
        spaces=[
            SyntheticSpace(
                name="Apartment 1", occupancy_type="Residential Unit", storey_index=1
            ),
            SyntheticSpace(
                name="Parking Bay 1", object_type="Parking", storey_index=0
            ),
        ],
        footprint_coords=[(-5.0, -4.0), (5.0, -4.0), (5.0, 4.0), (-5.0, 4.0)],
        world_origin=(HALIFAX_BASE_EASTING, HALIFAX_BASE_NORTHING, 0.0),
    )
    write_synthetic_ifc(spec, args.out)
    print(f"seed_e2e_submission_ifc: wrote {args.out} ({args.out.stat().st_size} bytes)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
