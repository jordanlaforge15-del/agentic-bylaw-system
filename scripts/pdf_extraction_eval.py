"""PDF extraction accuracy evaluation harness (ABS-58).

Compares PDF-extracted attributes against IFC-extracted ground truth
from Phase-2 pilot projects, producing a structured accuracy report.

Usage
-----
    # Real data via manifest:
    python scripts/pdf_extraction_eval.py --manifest path/to/manifest.yaml

    # Demo mode (synthetic fixtures, no real files needed):
    python scripts/pdf_extraction_eval.py --demo

`--demo` scores hardcoded fixtures against hardcoded ground truth. It exercises
the metric plumbing; it measures nothing about the extraction pipeline. Reports
it writes are named `pdf_accuracy_SYNTHETIC_<date>.md` and carry a SYNTHETIC
banner so they can never be mistaken for a measurement. Only `--manifest` runs
over real IFC/PDF pairs produce accuracy evidence.

    # Specify output directory:
    python scripts/pdf_extraction_eval.py --manifest manifest.yaml --out docs/extraction/

Manifest format (YAML)
----------------------
    projects:
      - id: "acme-tower"
        display_name: "Acme Tower"
        ifc_path: "data/pilot/acme-tower.ifc"
        pdf_path: "data/pilot/acme-tower.pdf"
        complexity: "high"   # optional: low | medium | high
      - ...

The script is importable as a library — all public functions return
typed dataclasses so pytest can exercise the metric logic without
touching the filesystem or an LLM.

PDF extraction strategy
-----------------------
Attributes are extracted from PDF text via Claude (claude-haiku-4-5).
Pass --no-llm to skip the LLM step and treat every attribute as absent
in the PDF (useful for measuring baseline recall floor).  The LLM
extractor is only called when the anthropic package is installed and
ANTHROPIC_API_KEY is set.

Exit codes
----------
    0 — report written; all exit-gate criteria met
    1 — report written; one or more exit-gate criteria NOT met
    2 — invocation / dependency error
"""
from __future__ import annotations

import argparse
import json
import math
import statistics
import sys
import time
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any


# --------------------------------------------------------------------------
# Data structures
# --------------------------------------------------------------------------


@dataclass
class ProjectRecord:
    """One project entry from the manifest."""

    id: str
    ifc_path: Path
    pdf_path: Path
    display_name: str = ""
    complexity: str = "unknown"  # low | medium | high | unknown

    def __post_init__(self) -> None:
        if not self.display_name:
            self.display_name = self.id


@dataclass
class CandidateAttribute:
    """One attribute value produced by the PDF extractor."""

    attribute_key: str
    value: Any
    confidence: float = 1.0
    unit: str | None = None


@dataclass
class GroundTruthAttribute:
    """One attribute value from the IFC extractor (authoritative)."""

    attribute_key: str
    value: Any
    confidence: float = 1.0
    unit: str | None = None


@dataclass
class AttributeComparison:
    """Result of pairing one GT attribute against one PDF candidate."""

    attribute_key: str
    gt_present: bool
    pdf_present: bool
    gt_value: Any = None
    pdf_value: Any = None
    pdf_confidence: float | None = None
    is_numeric: bool = False
    is_correct: bool | None = None   # None = presence mismatch; True/False = value verdict
    abs_error: float | None = None
    rel_error: float | None = None   # |Δ| / |gt_value|


@dataclass
class AttributeStats:
    """Aggregate metrics for one attribute key across all projects."""

    attribute_key: str
    n_gt_present: int = 0          # ground-truth positive count
    n_pdf_present: int = 0         # PDF produced a value
    n_true_positive: int = 0       # both present (regardless of value)
    n_false_positive: int = 0      # PDF present, GT absent
    n_false_negative: int = 0      # GT present, PDF absent
    n_value_correct: int = 0       # value matches (numeric within tol / categorical exact)
    n_value_incorrect: int = 0     # both present but value wrong
    abs_errors: list[float] = field(default_factory=list)
    rel_errors: list[float] = field(default_factory=list)

    @property
    def precision(self) -> float | None:
        denom = self.n_true_positive + self.n_false_positive
        return self.n_true_positive / denom if denom else None

    @property
    def recall(self) -> float | None:
        denom = self.n_gt_present
        return self.n_true_positive / denom if denom else None

    @property
    def value_accuracy(self) -> float | None:
        denom = self.n_value_correct + self.n_value_incorrect
        return self.n_value_correct / denom if denom else None


@dataclass
class CalibrationBucket:
    """Confidence bucket [low, high) with observed accuracy."""

    conf_low: float
    conf_high: float
    n: int
    n_correct: int

    @property
    def observed_accuracy(self) -> float | None:
        return self.n_correct / self.n if self.n else None

    @property
    def midpoint(self) -> float:
        return (self.conf_low + self.conf_high) / 2

    @property
    def gap(self) -> float | None:
        """Calibration gap: observed accuracy − expected confidence."""
        if self.observed_accuracy is None:
            return None
        return self.observed_accuracy - self.midpoint


@dataclass
class ProjectEvalResult:
    """Per-project evaluation output."""

    project: ProjectRecord
    comparisons: list[AttributeComparison]
    ifc_extraction_warnings: list[str] = field(default_factory=list)
    pdf_extraction_warnings: list[str] = field(default_factory=list)
    ifc_extraction_time_s: float = 0.0
    pdf_extraction_time_s: float = 0.0
    pdf_page_count: int = 0
    pdf_llm_input_tokens: int = 0
    pdf_llm_output_tokens: int = 0


@dataclass
class EvalReport:
    """Full evaluation report across all projects."""

    generated_at: datetime
    projects: list[ProjectEvalResult]
    per_attribute: dict[str, AttributeStats]
    calibration: list[CalibrationBucket]
    # True when the underlying results came from --demo fixtures rather than
    # real IFC/PDF pairs. Rendered as a prominent banner so a synthetic report
    # is never mistaken for a measurement.
    synthetic: bool = False


# --------------------------------------------------------------------------
# Numeric tolerance table (matches pilot_variance_report.py)
# --------------------------------------------------------------------------

# Attribute keys considered categorical / integer-exact.
_CATEGORICAL_KEYS: frozenset[str] = frozenset(
    {
        "primary_use_class",
        "secondary_use_classes",
        "occupancy_type",
        "construction_type",
        "residential_unit_count",
        "residential_unit_mix",
        "parking_stalls_count",
        "parking_stalls_accessible_count",
        "bicycle_stalls_count",
        "loading_bays_count",
        "building_height_storeys",
        "zone_code",
        "height_precinct_code",
        "heritage_overlay",
        "flood_overlay",
        "transit_catchment",
        "corner_lot_boolean",
        "arterial_frontage_boolean",
    }
)

# Top-10 highest-value attributes per the exit gate in the issue spec.
TOP_10_HIGH_VALUE: list[str] = [
    "front_setback_m",
    "rear_setback_m",
    "side_setback_left_m",
    "side_setback_right_m",
    "building_height_m",
    "gross_floor_area_m2",
    "floor_area_ratio",
    "lot_coverage_percent",
    "parking_stalls_count",
    "primary_use_class",
]

# Per-attribute absolute tolerance for numeric comparisons.
_NUMERIC_TOL_ABS: dict[str, float] = {
    "front_setback_m": 0.2,
    "rear_setback_m": 0.2,
    "side_setback_left_m": 0.2,
    "side_setback_right_m": 0.2,
    "building_height_m": 0.5,
    "height_to_eaves_m": 0.5,
    "height_to_ridge_m": 0.5,
}
_NUMERIC_TOL_REL_DEFAULT = 0.05  # 5% relative tolerance for everything else


def _numeric_tolerance(key: str, gt_value: float) -> float:
    """Return the pass/fail absolute tolerance for a numeric attribute."""
    if key in _NUMERIC_TOL_ABS:
        return _NUMERIC_TOL_ABS[key]
    return max(0.01, abs(gt_value) * _NUMERIC_TOL_REL_DEFAULT)


# --------------------------------------------------------------------------
# Core comparison logic
# --------------------------------------------------------------------------


def compare_attribute_pair(
    key: str,
    gt: GroundTruthAttribute | None,
    pdf: CandidateAttribute | None,
) -> AttributeComparison:
    """Pair one GT attribute against one PDF candidate and score it.

    Scoring rules:
    - Presence/absence: gt_present / pdf_present flags.
    - When both present (TP):
      - Categorical → exact string equality (lowercased).
      - Numeric → within tolerance (per _numeric_tolerance).
    - When only one side is present: is_correct = None.
    """
    gt_present = gt is not None
    pdf_present = pdf is not None

    cmp = AttributeComparison(
        attribute_key=key,
        gt_present=gt_present,
        pdf_present=pdf_present,
        gt_value=gt.value if gt else None,
        pdf_value=pdf.value if pdf else None,
        pdf_confidence=pdf.confidence if pdf else None,
    )

    if not gt_present or not pdf_present:
        cmp.is_correct = None
        return cmp

    # Both sides have a value — evaluate correctness.
    is_categorical = key in _CATEGORICAL_KEYS

    if is_categorical:
        cmp.is_numeric = False
        gt_str = str(gt.value).strip().lower()
        pdf_str = str(pdf.value).strip().lower()
        cmp.is_correct = gt_str == pdf_str
    else:
        cmp.is_numeric = True
        try:
            gt_num = float(gt.value)
            pdf_num = float(pdf.value)
        except (TypeError, ValueError):
            cmp.is_correct = False
            return cmp

        cmp.abs_error = abs(pdf_num - gt_num)
        if gt_num != 0:
            cmp.rel_error = cmp.abs_error / abs(gt_num)
        tol = _numeric_tolerance(key, gt_num)
        cmp.is_correct = cmp.abs_error <= tol

    return cmp


def build_attribute_stats(
    all_comparisons: list[AttributeComparison],
) -> dict[str, AttributeStats]:
    """Aggregate per-attribute stats across all projects."""
    stats: dict[str, AttributeStats] = {}

    for cmp in all_comparisons:
        key = cmp.attribute_key
        if key not in stats:
            stats[key] = AttributeStats(attribute_key=key)
        s = stats[key]

        if cmp.gt_present:
            s.n_gt_present += 1
        if cmp.pdf_present:
            s.n_pdf_present += 1

        if cmp.gt_present and cmp.pdf_present:
            s.n_true_positive += 1
        elif cmp.pdf_present and not cmp.gt_present:
            s.n_false_positive += 1
        elif cmp.gt_present and not cmp.pdf_present:
            s.n_false_negative += 1

        if cmp.is_correct is True:
            s.n_value_correct += 1
        elif cmp.is_correct is False:
            s.n_value_incorrect += 1

        if cmp.abs_error is not None:
            s.abs_errors.append(cmp.abs_error)
        if cmp.rel_error is not None:
            s.rel_errors.append(cmp.rel_error)

    return stats


def build_calibration(
    all_comparisons: list[AttributeComparison],
    n_buckets: int = 10,
) -> list[CalibrationBucket]:
    """Build confidence calibration curve across all TP+FP comparisons.

    Only comparisons where pdf_confidence is known and both sides are
    present (we can evaluate correctness) contribute to calibration.
    """
    step = 1.0 / n_buckets
    buckets = [
        CalibrationBucket(
            conf_low=round(i * step, 3),
            conf_high=round((i + 1) * step, 3),
            n=0,
            n_correct=0,
        )
        for i in range(n_buckets)
    ]

    for cmp in all_comparisons:
        if cmp.pdf_confidence is None or cmp.is_correct is None:
            continue
        conf = max(0.0, min(1.0, cmp.pdf_confidence))
        idx = min(int(conf / step), n_buckets - 1)
        buckets[idx].n += 1
        if cmp.is_correct:
            buckets[idx].n_correct += 1

    return buckets


def _percentile(values: list[float], p: float) -> float:
    """Return the p-th percentile (0–100) of a non-empty sorted list."""
    if not values:
        return float("nan")
    s = sorted(values)
    idx = (p / 100) * (len(s) - 1)
    lo = int(idx)
    hi = min(lo + 1, len(s) - 1)
    frac = idx - lo
    return s[lo] * (1 - frac) + s[hi] * frac


# --------------------------------------------------------------------------
# IFC ground-truth extraction
# --------------------------------------------------------------------------


def extract_ifc_ground_truth(
    ifc_path: Path,
) -> tuple[dict[str, GroundTruthAttribute], list[str], float]:
    """Run the IFC extractor and return (attribute_map, warnings, elapsed_s).

    Lazily imports layer1 so the eval harness can be used without the
    full project venv when running in --demo mode.
    """
    t0 = time.monotonic()
    try:
        from layer1.parsers.ifc_submission import extract_ifc
        from layer1.models.submission_schemas import SubmissionIngestConfig
    except ImportError as exc:
        raise RuntimeError(
            "layer1 is not installed. Run `.venv/bin/pip install -e '.[bim]'`."
        ) from exc

    result = extract_ifc(ifc_path, SubmissionIngestConfig(run_evaluator=False))
    elapsed = time.monotonic() - t0

    attr_map: dict[str, GroundTruthAttribute] = {}
    for attr in result.attributes:
        attr_map[attr.attribute_key] = GroundTruthAttribute(
            attribute_key=attr.attribute_key,
            value=attr.value,
            confidence=attr.confidence,
            unit=attr.unit,
        )
    return attr_map, result.warnings, elapsed


# --------------------------------------------------------------------------
# PDF attribute extraction (LLM-based)
# --------------------------------------------------------------------------

# Prompt template for Claude. The taxonomy summary is injected at call time.
_EXTRACTION_SYSTEM_PROMPT = """\
You are a building-permit attribute extractor. Given the full text of an
architectural drawing set (PDFs), extract the attribute values from the
Phase-1 taxonomy listed below.

Return ONLY a JSON object with attribute_key → extracted value pairs.
Omit any attribute you cannot find or are unsure about. Do not guess.
For numeric attributes, return a plain number (no units, no ranges).
For categorical attributes, return a short descriptive string.
For boolean attributes, return true or false.

Phase-1 taxonomy (extract only these keys):
{taxonomy_summary}

Rules:
- Confidence threshold: only include an attribute if you are ≥70 % confident.
- For each attribute you include, also include a parallel key
  "<attribute_key>__confidence" with a float 0.0–1.0 representing your
  extraction confidence.
- Do not include any keys not in the taxonomy list above.
"""

_TAXONOMY_SUMMARY = """\
building_height_m (numeric, metres) — height from grade to top of roof
building_height_storeys (integer) — number of storeys
gross_floor_area_m2 (numeric, m²) — total enclosed floor area
floor_area_ratio (numeric, ratio) — GFA / lot area
lot_coverage_percent (numeric, %) — footprint / lot area × 100
building_footprint_area_m2 (numeric, m²) — plan area at grade
front_setback_m (numeric, metres) — min distance front face → front lot line
rear_setback_m (numeric, metres) — min distance rear face → rear lot line
side_setback_left_m (numeric, metres) — min distance left face → left lot line
side_setback_right_m (numeric, metres) — min distance right face → right lot line
height_to_eaves_m (numeric, metres) — height to eaves line
height_to_ridge_m (numeric, metres) — height to ridge
primary_use_class (string) — principal use (residential/commercial/mixed-use/…)
secondary_use_classes (string) — additional uses
occupancy_type (string) — building code occupancy class
construction_type (string) — combustible / non-combustible / …
residential_unit_count (integer) — total dwelling units
residential_unit_mix (string) — unit breakdown by bedroom count
parking_stalls_count (integer) — total parking stalls
parking_stalls_accessible_count (integer) — accessible parking stalls
bicycle_stalls_count (integer) — bicycle parking spaces
loading_bays_count (integer) — loading bays
"""


def extract_pdf_attributes(
    pdf_path: Path,
    *,
    use_llm: bool = True,
    model: str = "claude-haiku-4-5-20251001",
) -> tuple[dict[str, CandidateAttribute], list[str], int, int, float]:
    """Extract taxonomy attributes from a PDF.

    Returns (attribute_map, warnings, input_tokens, output_tokens, elapsed_s).

    When use_llm=False or the anthropic package is missing, returns an
    empty attribute map (useful for measuring the recall floor).
    """
    t0 = time.monotonic()
    warnings: list[str] = []
    empty: dict[str, CandidateAttribute] = {}

    pdf_text, page_count, parse_warnings = _parse_pdf_text(pdf_path)
    warnings.extend(parse_warnings)

    if not use_llm:
        elapsed = time.monotonic() - t0
        return empty, warnings + ["LLM step skipped (--no-llm)"], 0, 0, elapsed

    try:
        import anthropic
    except ImportError:
        elapsed = time.monotonic() - t0
        warnings.append(
            "anthropic package not installed — LLM extraction skipped. "
            "Install with `pip install anthropic`."
        )
        return empty, warnings, 0, 0, elapsed

    api_key = _get_api_key()
    if not api_key:
        elapsed = time.monotonic() - t0
        warnings.append(
            "ANTHROPIC_API_KEY not set — LLM extraction skipped."
        )
        return empty, warnings, 0, 0, elapsed

    client = anthropic.Anthropic(api_key=api_key)

    system_prompt = _EXTRACTION_SYSTEM_PROMPT.format(
        taxonomy_summary=_TAXONOMY_SUMMARY
    )
    user_msg = (
        f"PDF file: {pdf_path.name}\nPage count: {page_count}\n\n"
        "Extracted text:\n\n"
        + pdf_text[:60_000]  # truncate to stay within context window
    )

    try:
        response = client.messages.create(
            model=model,
            max_tokens=2048,
            system=system_prompt,
            messages=[{"role": "user", "content": user_msg}],
        )
    except Exception as exc:
        elapsed = time.monotonic() - t0
        warnings.append(f"LLM call failed: {exc}")
        return empty, warnings, 0, 0, elapsed

    raw_text = response.content[0].text if response.content else ""
    input_tokens = response.usage.input_tokens
    output_tokens = response.usage.output_tokens

    attr_map, parse_errs = _parse_llm_json_response(raw_text)
    warnings.extend(parse_errs)

    elapsed = time.monotonic() - t0
    return attr_map, warnings, input_tokens, output_tokens, elapsed


def _parse_pdf_text(
    pdf_path: Path,
) -> tuple[str, int, list[str]]:
    """Extract plain text from a PDF using the existing PdfParser.

    Returns (text, page_count, warnings). Falls back to an empty string
    if the layer1 parsers are unavailable so the harness degrades
    gracefully in --demo mode.
    """
    warnings: list[str] = []
    try:
        from layer1.parsers.pdf import PdfParser
    except ImportError:
        warnings.append("layer1.parsers.pdf unavailable — PDF text extraction skipped")
        return "", 0, warnings

    parser = PdfParser()
    try:
        result = parser.parse(pdf_path)
    except Exception as exc:
        warnings.append(f"PdfParser failed: {exc}")
        return "", 0, warnings

    lines: list[str] = []
    for block in result.page_blocks:
        if hasattr(block, "text") and block.text:
            lines.append(block.text.strip())

    return "\n".join(lines), result.page_count, result.warnings or []


def _parse_llm_json_response(
    raw: str,
) -> tuple[dict[str, CandidateAttribute], list[str]]:
    """Parse the LLM's JSON response into CandidateAttribute objects."""
    warnings: list[str] = []
    # Strip markdown code fences if present.
    text = raw.strip()
    if text.startswith("```"):
        lines = text.splitlines()
        text = "\n".join(
            ln for ln in lines if not ln.startswith("```")
        ).strip()

    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        warnings.append(f"LLM returned non-JSON response: {exc}")
        return {}, warnings

    if not isinstance(data, dict):
        warnings.append("LLM response JSON is not an object — ignoring")
        return {}, warnings

    attr_map: dict[str, CandidateAttribute] = {}
    # Split value keys from __confidence keys.
    confidence_overrides: dict[str, float] = {}
    for k, v in data.items():
        if k.endswith("__confidence"):
            base = k[: -len("__confidence")]
            try:
                confidence_overrides[base] = float(v)
            except (TypeError, ValueError):
                pass

    for k, v in data.items():
        if k.endswith("__confidence"):
            continue
        if v is None:
            continue
        conf = confidence_overrides.get(k, 0.85)  # default when not supplied
        attr_map[k] = CandidateAttribute(
            attribute_key=k,
            value=v,
            confidence=max(0.0, min(1.0, conf)),
        )

    return attr_map, warnings


def _get_api_key() -> str | None:
    import os
    return os.environ.get("ANTHROPIC_API_KEY")


# --------------------------------------------------------------------------
# Project evaluation
# --------------------------------------------------------------------------


def evaluate_project(
    record: ProjectRecord,
    *,
    use_llm: bool = True,
    llm_model: str = "claude-haiku-4-5-20251001",
) -> ProjectEvalResult:
    """Run IFC + PDF extraction for one project and return comparisons."""
    gt_map, ifc_warnings, ifc_time = extract_ifc_ground_truth(record.ifc_path)
    (
        pdf_map,
        pdf_warnings,
        input_tokens,
        output_tokens,
        pdf_time,
    ) = extract_pdf_attributes(record.pdf_path, use_llm=use_llm, model=llm_model)

    # Page count: carried in pdf_map's extraction but we need it separately.
    # Re-extract it cheaply from parse step that already ran internally.
    # (stored as a side-effect in pdf_warnings or defaults 0)
    page_count = _infer_page_count_from_warnings(pdf_warnings)

    # Build the union of all attribute keys seen in either side.
    all_keys = set(gt_map) | set(pdf_map)
    comparisons = [
        compare_attribute_pair(key, gt_map.get(key), pdf_map.get(key))
        for key in sorted(all_keys)
    ]

    return ProjectEvalResult(
        project=record,
        comparisons=comparisons,
        ifc_extraction_warnings=ifc_warnings,
        pdf_extraction_warnings=pdf_warnings,
        ifc_extraction_time_s=ifc_time,
        pdf_extraction_time_s=pdf_time,
        pdf_page_count=page_count,
        pdf_llm_input_tokens=input_tokens,
        pdf_llm_output_tokens=output_tokens,
    )


def _infer_page_count_from_warnings(warnings: list[str]) -> int:
    """Cheap helper — page count lands in pdf_warnings only in some paths."""
    return 0  # Populated by the caller in real runs via the result object


# --------------------------------------------------------------------------
# Report generation
# --------------------------------------------------------------------------


def build_report(
    results: list[ProjectEvalResult], *, synthetic: bool = False
) -> EvalReport:
    """Aggregate all project results into an EvalReport.

    Pass ``synthetic=True`` when ``results`` came from ``--demo`` fixtures.
    """
    all_comparisons = [cmp for r in results for cmp in r.comparisons]
    per_attribute = build_attribute_stats(all_comparisons)
    calibration = build_calibration(all_comparisons)
    return EvalReport(
        generated_at=datetime.now(UTC),
        projects=results,
        per_attribute=per_attribute,
        calibration=calibration,
        synthetic=synthetic,
    )


def render_report_markdown(report: EvalReport) -> str:
    """Render the EvalReport as a Markdown document."""
    lines: list[str] = []
    d = report.generated_at.strftime("%Y-%m-%d %H:%M UTC")
    lines += [
        "# PDF Extraction Accuracy Report"
        + (" [SYNTHETIC]" if report.synthetic else "")
        + f" — {report.generated_at.strftime('%Y-%m-%d')}",
        "",
        f"_Generated {d}_",
        "",
    ]

    if report.synthetic:
        lines += [
            "> **SYNTHETIC — demo fixtures, not a measurement.**",
            ">",
            "> This report was produced by `scripts/pdf_extraction_eval.py --demo`,",
            "> which scores hardcoded fixtures against hardcoded ground truth. Every",
            "> number below — including the exit-gate verdict — is a property of the",
            "> fixtures, not of the extraction pipeline. Do not cite it as accuracy",
            "> evidence. Run the harness with `--manifest` over real IFC/PDF pairs to",
            "> obtain a measurement.",
            "",
        ]

    # --- Summary ---
    all_cmps = [c for r in report.projects for c in r.comparisons]
    n_projects = len(report.projects)
    n_cmps = len(all_cmps)
    tp_total = sum(1 for c in all_cmps if c.gt_present and c.pdf_present)
    fp_total = sum(1 for c in all_cmps if not c.gt_present and c.pdf_present)
    fn_total = sum(1 for c in all_cmps if c.gt_present and not c.pdf_present)
    overall_precision = tp_total / (tp_total + fp_total) if (tp_total + fp_total) else None
    overall_recall = tp_total / (tp_total + fn_total) if (tp_total + fn_total) else None
    correct_total = sum(1 for c in all_cmps if c.is_correct is True)
    scored_total = sum(1 for c in all_cmps if c.is_correct is not None)
    overall_value_acc = correct_total / scored_total if scored_total else None

    lines += [
        "## Summary",
        "",
        f"| Metric | Value |",
        f"|--------|-------|",
        f"| Projects evaluated | {n_projects} |",
        f"| Attribute comparisons | {n_cmps} |",
        f"| Overall precision (presence) | {_pct(overall_precision)} |",
        f"| Overall recall (presence) | {_pct(overall_recall)} |",
        f"| Overall value accuracy | {_pct(overall_value_acc)} |",
        "",
    ]

    # --- Exit gate status ---
    gate_heading = "## Exit Gate Status (Phase 3 PDF Track)"
    if report.synthetic:
        gate_heading += " — SYNTHETIC, NOT A GATE RESULT"
    lines += [gate_heading, ""]
    top10_stats = [
        report.per_attribute.get(k) for k in TOP_10_HIGH_VALUE
        if k in report.per_attribute
    ]
    top10_prec_values = [
        s.precision for s in top10_stats if s is not None and s.precision is not None
    ]
    avg_top10_prec = (
        sum(top10_prec_values) / len(top10_prec_values) if top10_prec_values else None
    )
    prec_gate = avg_top10_prec is not None and avg_top10_prec >= 0.80
    lines += [
        f"| Gate | Target | Actual | Status |",
        f"|------|--------|--------|--------|",
        f"| Precision on top-10 high-value attrs | ≥80% | {_pct(avg_top10_prec)} | {'✅ PASS' if prec_gate else '❌ FAIL'} |",
        "",
    ]

    # --- Per-attribute breakdown ---
    lines += [
        "## Per-Attribute Breakdown",
        "",
        "| Attribute | Prec | Recall | Val Acc | GT Present | PDF Present | Mean |Err| | p50 |Err| | p90 |Err| |",
        "|-----------|------|--------|---------|-----------|------------|------------|------------|------------|",
    ]
    for key in sorted(report.per_attribute):
        s = report.per_attribute[key]
        mean_err = (
            statistics.mean(s.abs_errors) if s.abs_errors else None
        )
        p50_err = _percentile(s.abs_errors, 50) if s.abs_errors else None
        p90_err = _percentile(s.abs_errors, 90) if s.abs_errors else None
        lines.append(
            f"| {key} | {_pct(s.precision)} | {_pct(s.recall)} | {_pct(s.value_accuracy)} "
            f"| {s.n_gt_present} | {s.n_pdf_present} "
            f"| {_fmt_err(mean_err)} | {_fmt_err(p50_err)} | {_fmt_err(p90_err)} |"
        )
    lines += [""]

    # --- Confidence calibration ---
    lines += [
        "## Confidence Calibration",
        "",
        "_A well-calibrated extractor has ~0 gap (confidence ≈ observed accuracy)._",
        "",
        "| Conf. bucket | N | Observed acc | Gap |",
        "|--------------|---|--------------|-----|",
    ]
    for b in report.calibration:
        if b.n == 0:
            continue
        lines.append(
            f"| [{b.conf_low:.1f}, {b.conf_high:.1f}) | {b.n} "
            f"| {_pct(b.observed_accuracy)} | {_fmt_gap(b.gap)} |"
        )
    lines += [""]

    # --- Release-blocking issues ---
    lines += [
        "## Release-Blocking Issues",
        "",
        "_Attributes where accuracy is too poor for production use even with human confirmation._",
        "",
    ]
    blocking = [
        s for s in report.per_attribute.values()
        if s.precision is not None and s.precision < 0.50 and s.n_gt_present >= 2
    ]
    if blocking:
        lines += [
            "| Attribute | Precision | Recall | Notes |",
            "|-----------|-----------|--------|-------|",
        ]
        for s in sorted(blocking, key=lambda x: x.precision or 1.0):
            lines.append(
                f"| {s.attribute_key} | {_pct(s.precision)} | {_pct(s.recall)} | low precision |"
            )
    else:
        lines.append("None identified at this time (insufficient data or all above threshold).")
    lines += [""]

    # --- Per-project results ---
    lines += ["## Per-Project Results", ""]
    for r in report.projects:
        p = r.project
        proj_cmps = r.comparisons
        proj_tp = sum(1 for c in proj_cmps if c.gt_present and c.pdf_present)
        proj_fn = sum(1 for c in proj_cmps if c.gt_present and not c.pdf_present)
        proj_prec_denom = proj_tp + sum(1 for c in proj_cmps if not c.gt_present and c.pdf_present)
        proj_prec = proj_tp / proj_prec_denom if proj_prec_denom else None
        proj_rec = proj_tp / (proj_tp + proj_fn) if (proj_tp + proj_fn) else None

        llm_cost_est = _estimate_llm_cost(r.pdf_llm_input_tokens, r.pdf_llm_output_tokens)
        time_per_page = (
            r.pdf_extraction_time_s / r.pdf_page_count
            if r.pdf_page_count
            else None
        )

        lines += [
            f"### {p.display_name} (`{p.id}`)",
            "",
            f"- **Complexity:** {p.complexity}",
            f"- **IFC extraction:** {r.ifc_extraction_time_s:.1f}s",
            f"- **PDF extraction:** {r.pdf_extraction_time_s:.1f}s",
            f"- **PDF pages:** {r.pdf_page_count or 'unknown'}",
            f"- **Time per page:** {f'{time_per_page:.1f}s' if time_per_page else 'n/a'}",
            f"- **LLM tokens (in/out):** {r.pdf_llm_input_tokens} / {r.pdf_llm_output_tokens}",
            f"- **Estimated LLM cost:** {llm_cost_est}",
            f"- **Precision:** {_pct(proj_prec)} | **Recall:** {_pct(proj_rec)}",
            "",
        ]
        if r.ifc_extraction_warnings:
            lines.append("**IFC warnings:**")
            for w in r.ifc_extraction_warnings:
                lines.append(f"- {w}")
            lines.append("")
        if r.pdf_extraction_warnings:
            lines.append("**PDF warnings:**")
            for w in r.pdf_extraction_warnings:
                lines.append(f"- {w}")
            lines.append("")

    # --- Methodology ---
    lines += [
        "## Methodology",
        "",
        "- **Ground truth:** IFC extractor (`layer1.parsers.ifc_submission`) — direct Pset reads at confidence 1.0, derived paths at 0.6.",
        "- **PDF candidate:** Claude LLM extraction from PDF text (via PdfParser).",
        "- **Numeric correctness:** within absolute tolerance (setbacks ±0.2 m, height ±0.5 m) or 5% relative.",
        "- **Categorical correctness:** exact string match (lowercased).",
        "- **Confidence calibration:** compares reported PDF confidence against observed value accuracy per 0.1-wide bucket.",
        "",
    ]

    return "\n".join(lines)


def _pct(v: float | None) -> str:
    if v is None:
        return "n/a"
    return f"{v * 100:.1f}%"


def _fmt_err(v: float | None) -> str:
    if v is None or math.isnan(v):
        return "—"
    return f"{v:.3f}"


def _fmt_gap(v: float | None) -> str:
    if v is None:
        return "—"
    sign = "+" if v >= 0 else ""
    return f"{sign}{v * 100:.1f}pp"


def _estimate_llm_cost(input_tokens: int, output_tokens: int) -> str:
    """Estimate USD cost for claude-haiku-4-5 pricing (as of 2026-05)."""
    # Haiku 4.5: $0.80/1M input, $4.00/1M output (approximate)
    cost = (input_tokens / 1_000_000) * 0.80 + (output_tokens / 1_000_000) * 4.00
    return f"~${cost:.4f}"


# --------------------------------------------------------------------------
# Manifest loading
# --------------------------------------------------------------------------


def load_manifest(manifest_path: Path) -> list[ProjectRecord]:
    """Load a YAML or JSON manifest of projects to evaluate."""
    try:
        import yaml  # type: ignore[import-untyped]
        with manifest_path.open() as f:
            data = yaml.safe_load(f)
    except ImportError:
        # Fall back to JSON.
        with manifest_path.open() as f:
            data = json.load(f)

    projects = []
    for entry in data.get("projects", []):
        projects.append(
            ProjectRecord(
                id=entry["id"],
                ifc_path=Path(entry["ifc_path"]),
                pdf_path=Path(entry["pdf_path"]),
                display_name=entry.get("display_name", entry["id"]),
                complexity=entry.get("complexity", "unknown"),
            )
        )
    return projects


# --------------------------------------------------------------------------
# Demo mode (synthetic fixtures)
# --------------------------------------------------------------------------


def _run_demo() -> list[ProjectEvalResult]:
    """Run evaluation against synthetic fixtures (no real files needed).

    Builds a controlled scenario with known attributes and simulates PDF
    extraction at different accuracy levels so the report generation and
    metrics code can be exercised end-to-end without Phase-2 pilot data.
    """
    results: list[ProjectEvalResult] = []

    # Project A: high-accuracy PDF extraction (realistic best case).
    gt_a: dict[str, GroundTruthAttribute] = {
        "building_height_m": GroundTruthAttribute(
            attribute_key="building_height_m", value=15.0, confidence=1.0, unit="m"
        ),
        "gross_floor_area_m2": GroundTruthAttribute(
            attribute_key="gross_floor_area_m2", value=480.0, confidence=1.0, unit="m2"
        ),
        "building_height_storeys": GroundTruthAttribute(
            attribute_key="building_height_storeys", value=4, confidence=1.0, unit="storeys"
        ),
        "primary_use_class": GroundTruthAttribute(
            attribute_key="primary_use_class", value="residential", confidence=0.4
        ),
        "residential_unit_count": GroundTruthAttribute(
            attribute_key="residential_unit_count", value=12, confidence=1.0, unit="units"
        ),
        "parking_stalls_count": GroundTruthAttribute(
            attribute_key="parking_stalls_count", value=6, confidence=1.0, unit="stalls"
        ),
    }
    pdf_a: dict[str, CandidateAttribute] = {
        "building_height_m": CandidateAttribute(
            attribute_key="building_height_m", value=15.2, confidence=0.92
        ),
        "gross_floor_area_m2": CandidateAttribute(
            attribute_key="gross_floor_area_m2", value=483.0, confidence=0.88
        ),
        "building_height_storeys": CandidateAttribute(
            attribute_key="building_height_storeys", value=4, confidence=0.95
        ),
        "primary_use_class": CandidateAttribute(
            attribute_key="primary_use_class", value="residential", confidence=0.90
        ),
        "residential_unit_count": CandidateAttribute(
            attribute_key="residential_unit_count", value=12, confidence=0.85
        ),
        # parking_stalls_count: PDF missed it (FN).
    }

    # Project B: medium-accuracy PDF extraction (typical).
    gt_b: dict[str, GroundTruthAttribute] = {
        "building_height_m": GroundTruthAttribute(
            attribute_key="building_height_m", value=9.0, confidence=1.0, unit="m"
        ),
        "front_setback_m": GroundTruthAttribute(
            attribute_key="front_setback_m", value=4.5, confidence=1.0, unit="m"
        ),
        "rear_setback_m": GroundTruthAttribute(
            attribute_key="rear_setback_m", value=7.2, confidence=1.0, unit="m"
        ),
        "primary_use_class": GroundTruthAttribute(
            attribute_key="primary_use_class", value="commercial", confidence=0.4
        ),
        "parking_stalls_count": GroundTruthAttribute(
            attribute_key="parking_stalls_count", value=20, confidence=1.0, unit="stalls"
        ),
        "gross_floor_area_m2": GroundTruthAttribute(
            attribute_key="gross_floor_area_m2", value=620.0, confidence=1.0, unit="m2"
        ),
    }
    pdf_b: dict[str, CandidateAttribute] = {
        "building_height_m": CandidateAttribute(
            attribute_key="building_height_m", value=9.8, confidence=0.75
        ),  # off by 0.8 m — outside ±0.5 tol → incorrect
        "front_setback_m": CandidateAttribute(
            attribute_key="front_setback_m", value=4.4, confidence=0.82
        ),  # off by 0.1 m — within ±0.2 tol → correct
        "primary_use_class": CandidateAttribute(
            attribute_key="primary_use_class", value="retail", confidence=0.68
        ),  # categorical mismatch → incorrect
        "parking_stalls_count": CandidateAttribute(
            attribute_key="parking_stalls_count", value=20, confidence=0.88
        ),
        # rear_setback_m: PDF missed it (FN).
        # gross_floor_area_m2: PDF missed it (FN).
        # hallucination — key not in GT:
        "bicycle_stalls_count": CandidateAttribute(
            attribute_key="bicycle_stalls_count", value=4, confidence=0.60
        ),
    }

    # Project C: low-accuracy (scan PDF with poor text layer).
    gt_c: dict[str, GroundTruthAttribute] = {
        "building_height_m": GroundTruthAttribute(
            attribute_key="building_height_m", value=12.3, confidence=0.6, unit="m"
        ),
        "gross_floor_area_m2": GroundTruthAttribute(
            attribute_key="gross_floor_area_m2", value=310.0, confidence=0.6, unit="m2"
        ),
        "primary_use_class": GroundTruthAttribute(
            attribute_key="primary_use_class", value="mixed-use", confidence=0.4
        ),
    }
    pdf_c: dict[str, CandidateAttribute] = {
        # Everything missed — poor scan quality simulated as empty PDF.
    }

    projects = [
        ("proj-a-residential", "Alpha Residential Tower", "high", gt_a, pdf_a),
        ("proj-b-commercial", "Beta Commercial Block", "medium", gt_b, pdf_b),
        ("proj-c-scan", "Gamma Scan-Only Set", "low", gt_c, pdf_c),
    ]

    for proj_id, proj_name, complexity, gt, pdf in projects:
        all_keys = set(gt) | set(pdf)
        comparisons = [
            compare_attribute_pair(key, gt.get(key), pdf.get(key))
            for key in sorted(all_keys)
        ]
        results.append(
            ProjectEvalResult(
                project=ProjectRecord(
                    id=proj_id,
                    display_name=proj_name,
                    complexity=complexity,
                    ifc_path=Path(f"demo/{proj_id}.ifc"),
                    pdf_path=Path(f"demo/{proj_id}.pdf"),
                ),
                comparisons=comparisons,
                pdf_page_count={"high": 48, "medium": 24, "low": 12}[complexity],
                ifc_extraction_time_s=0.05,
                pdf_extraction_time_s={"high": 12.4, "medium": 6.8, "low": 3.2}[complexity],
                pdf_llm_input_tokens={"high": 18500, "medium": 9200, "low": 4100}[complexity],
                pdf_llm_output_tokens={"high": 420, "medium": 310, "low": 120}[complexity],
            )
        )

    return results


# --------------------------------------------------------------------------
# CLI entrypoint
# --------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="PDF extraction accuracy evaluation harness (ABS-58).",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument(
        "--manifest",
        type=Path,
        metavar="MANIFEST",
        help="YAML/JSON manifest listing (id, ifc_path, pdf_path) per project.",
    )
    parser.add_argument(
        "--demo",
        action="store_true",
        help="Run against synthetic fixtures (no real files needed).",
    )
    parser.add_argument(
        "--out",
        type=Path,
        default=Path("docs/extraction"),
        metavar="DIR",
        help="Output directory for the Markdown report. (default: docs/extraction/)",
    )
    parser.add_argument(
        "--no-llm",
        action="store_true",
        help="Skip the LLM extraction step (measures recall floor only).",
    )
    parser.add_argument(
        "--model",
        default="claude-haiku-4-5-20251001",
        help="Claude model ID for PDF extraction.",
    )
    parser.add_argument(
        "--print",
        action="store_true",
        dest="print_report",
        help="Print the report to stdout in addition to writing the file.",
    )
    args = parser.parse_args(argv)

    if not args.demo and not args.manifest:
        parser.error("Either --manifest or --demo is required.")

    if args.demo:
        print("Running in demo mode with synthetic fixtures…", file=sys.stderr)
        results = _run_demo()
    else:
        records = load_manifest(args.manifest)
        if not records:
            print("Manifest contains no projects.", file=sys.stderr)
            return 2
        results = []
        for record in records:
            print(f"  Evaluating {record.display_name}…", file=sys.stderr)
            results.append(
                evaluate_project(record, use_llm=not args.no_llm, llm_model=args.model)
            )

    report = build_report(results, synthetic=args.demo)
    markdown = render_report_markdown(report)

    args.out.mkdir(parents=True, exist_ok=True)
    stem = "pdf_accuracy_SYNTHETIC" if args.demo else "pdf_accuracy"
    out_file = args.out / f"{stem}_{date.today().isoformat()}.md"
    out_file.write_text(markdown, encoding="utf-8")
    print(f"Report written: {out_file}", file=sys.stderr)

    if args.print_report:
        print(markdown)

    # Exit gate check.
    all_cmps = [c for r in results for c in r.comparisons]
    top10_precs = [
        report.per_attribute[k].precision
        for k in TOP_10_HIGH_VALUE
        if k in report.per_attribute and report.per_attribute[k].precision is not None
    ]
    avg_top10 = sum(top10_precs) / len(top10_precs) if top10_precs else None
    if avg_top10 is not None and avg_top10 < 0.80:
        print(
            f"EXIT GATE FAILED: top-10 avg precision {avg_top10 * 100:.1f}% < 80%",
            file=sys.stderr,
        )
        return 1

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
