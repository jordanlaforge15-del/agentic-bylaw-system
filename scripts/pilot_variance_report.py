"""Pilot variance report — manual vs. automated per attribute.

Reads a CSV produced during the Phase-2 pilot (one row per
attribute-measurement; columns documented in
`docs/pilot/halifax-pilot-runbook.md`) and emits a console-friendly
report of where the automated pipeline differs from the customer's
hand-measurement.

Pure stdlib — no project deps. Runs against the system python so the
operator doesn't need to provision a venv to use it.

Usage:

    python scripts/pilot_variance_report.py docs/pilots/data/pilot_acme_proj1.csv
    python scripts/pilot_variance_report.py path/to/pilot.csv --tolerance-m 0.2

The report breaks the rows into three buckets:

* **PASS** — numeric attrs within ±tolerance, categorical attrs that
  match exactly.
* **NEAR MISS** — numeric attrs within ±2×tolerance but outside
  ±tolerance. Worth a look but not necessarily a bug.
* **FAIL** — outside the soft band, or categorical mismatch.

Setbacks (`*_setback_m`) default to ±0.2 m per the scorecard. Override
with `--tolerance-m`. Other numeric attrs use a 5% relative tolerance.
"""
from __future__ import annotations

import argparse
import csv
import math
import sys
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any


# Per-attribute numeric tolerance overrides. Anything not listed
# defaults to a 5% relative tolerance with a 1.0-unit floor.
_NUMERIC_TOLERANCES_ABS: dict[str, float] = {
    "front_setback_m": 0.2,
    "rear_setback_m": 0.2,
    "side_setback_left_m": 0.2,
    "side_setback_right_m": 0.2,
    "building_height_m": 0.5,
}

_CATEGORICAL_KEYS = {
    "primary_use_class",
    "corner_lot_boolean",
    "arterial_frontage_boolean",
    "residential_unit_count",
    "parking_stalls_count",
    "bicycle_stalls_count",
    "building_height_storeys",
}


@dataclass
class _RowVerdict:
    attribute_key: str
    manual_value: Any
    automated_value: Any
    delta: float | None
    verdict: str  # "pass" | "near_miss" | "fail" | "missing"
    note: str = ""


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Compare manual vs. automated values from a pilot CSV "
            "and report variance per attribute."
        )
    )
    parser.add_argument(
        "csv_path", type=Path, help="Path to the per-project pilot CSV."
    )
    parser.add_argument(
        "--tolerance-m",
        type=float,
        default=None,
        help=(
            "Override the per-setback absolute tolerance in metres. "
            "Defaults to 0.2 m (matches the scorecard)."
        ),
    )
    parser.add_argument(
        "--exit-on-fail",
        action="store_true",
        help="Return non-zero exit code when any row is FAIL or near_miss.",
    )
    args = parser.parse_args()

    if not args.csv_path.exists():
        print(f"ERROR: {args.csv_path} not found.", file=sys.stderr)
        return 2

    if args.tolerance_m is not None:
        for key in (
            "front_setback_m",
            "rear_setback_m",
            "side_setback_left_m",
            "side_setback_right_m",
        ):
            _NUMERIC_TOLERANCES_ABS[key] = args.tolerance_m

    verdicts = _evaluate_csv(args.csv_path)
    _print_report(args.csv_path, verdicts)

    if args.exit_on_fail and any(
        v.verdict in ("fail", "near_miss", "missing") for v in verdicts
    ):
        return 1
    return 0


def _evaluate_csv(csv_path: Path) -> list[_RowVerdict]:
    verdicts: list[_RowVerdict] = []
    with csv_path.open() as f:
        reader = csv.DictReader(f)
        required = {"attribute_key", "manual_value", "automated_value"}
        if not required.issubset(set(reader.fieldnames or [])):
            missing = required - set(reader.fieldnames or [])
            raise SystemExit(
                f"CSV {csv_path} is missing required columns: {sorted(missing)}"
            )
        for row in reader:
            verdicts.append(_evaluate_row(row))
    return verdicts


def _evaluate_row(row: dict[str, str]) -> _RowVerdict:
    key = row["attribute_key"]
    manual_raw = row.get("manual_value", "").strip()
    auto_raw = row.get("automated_value", "").strip()

    if not manual_raw and not auto_raw:
        return _RowVerdict(
            attribute_key=key,
            manual_value=None,
            automated_value=None,
            delta=None,
            verdict="missing",
            note="both manual and automated values are blank",
        )
    if not manual_raw:
        return _RowVerdict(
            attribute_key=key,
            manual_value=None,
            automated_value=auto_raw,
            delta=None,
            verdict="missing",
            note="no manual ground truth — operator did not measure",
        )
    if not auto_raw:
        return _RowVerdict(
            attribute_key=key,
            manual_value=manual_raw,
            automated_value=None,
            delta=None,
            verdict="fail",
            note="pipeline did not produce a value",
        )

    if key in _CATEGORICAL_KEYS:
        return _evaluate_categorical(key, manual_raw, auto_raw)
    return _evaluate_numeric(key, manual_raw, auto_raw)


def _evaluate_categorical(
    key: str, manual_raw: str, auto_raw: str
) -> _RowVerdict:
    manual = manual_raw.strip().lower()
    auto = auto_raw.strip().lower()
    if manual == auto:
        return _RowVerdict(
            attribute_key=key,
            manual_value=manual,
            automated_value=auto,
            delta=None,
            verdict="pass",
        )
    return _RowVerdict(
        attribute_key=key,
        manual_value=manual,
        automated_value=auto,
        delta=None,
        verdict="fail",
        note="categorical mismatch",
    )


def _evaluate_numeric(
    key: str, manual_raw: str, auto_raw: str
) -> _RowVerdict:
    try:
        manual = float(manual_raw)
        auto = float(auto_raw)
    except ValueError:
        return _RowVerdict(
            attribute_key=key,
            manual_value=manual_raw,
            automated_value=auto_raw,
            delta=None,
            verdict="fail",
            note="non-numeric value in a numeric attribute",
        )
    delta = abs(auto - manual)
    abs_tol = _NUMERIC_TOLERANCES_ABS.get(key)
    if abs_tol is None:
        abs_tol = max(1.0, manual * 0.05)
    if delta <= abs_tol:
        verdict = "pass"
    elif delta <= 2 * abs_tol:
        verdict = "near_miss"
    else:
        verdict = "fail"
    return _RowVerdict(
        attribute_key=key,
        manual_value=manual,
        automated_value=auto,
        delta=delta,
        verdict=verdict,
        note=f"|Δ|={delta:.3f}, tol=±{abs_tol:.3f}",
    )


def _print_report(csv_path: Path, verdicts: list[_RowVerdict]) -> None:
    counts = Counter(v.verdict for v in verdicts)
    total = len(verdicts)
    print(f"Pilot variance report — {csv_path.name}")
    print(f"  Rows: {total}  |  "
          f"pass={counts.get('pass', 0)}  "
          f"near_miss={counts.get('near_miss', 0)}  "
          f"fail={counts.get('fail', 0)}  "
          f"missing={counts.get('missing', 0)}")
    if total > 0:
        pass_pct = 100.0 * counts.get("pass", 0) / total
        print(f"  Pass rate: {pass_pct:.1f}%")
    print()
    print(
        f"{'attribute_key':<30}  {'manual':<12}  {'automated':<12}  "
        f"{'verdict':<10}  note"
    )
    print("-" * 100)
    for v in verdicts:
        manual = _fmt(v.manual_value)
        auto = _fmt(v.automated_value)
        print(
            f"{v.attribute_key:<30}  {manual:<12}  {auto:<12}  "
            f"{v.verdict:<10}  {v.note}"
        )


def _fmt(value: Any) -> str:
    if value is None:
        return "—"
    if isinstance(value, float):
        return f"{value:g}"
    return str(value)


if __name__ == "__main__":
    raise SystemExit(main())
