"""ABS-58: Tests for the PDF extraction accuracy evaluation harness.

All tests are self-contained — no IFC files, no PDF files, no LLM calls.
They exercise the comparison logic, metric calculation, calibration curve,
report generation, and CLI in isolation so the harness can be run in CI
without pilot data.
"""
from __future__ import annotations

import math
from pathlib import Path

import pytest

from pdf_extraction_eval import (
    TOP_10_HIGH_VALUE,
    AttributeComparison,
    AttributeStats,
    CalibrationBucket,
    CandidateAttribute,
    EvalReport,
    GroundTruthAttribute,
    ProjectEvalResult,
    ProjectRecord,
    _CATEGORICAL_KEYS,
    _NUMERIC_TOL_ABS,
    _estimate_llm_cost,
    _parse_llm_json_response,
    _percentile,
    build_attribute_stats,
    build_calibration,
    build_report,
    compare_attribute_pair,
    render_report_markdown,
    _run_demo,
    main,
)


# --------------------------------------------------------------------------
# compare_attribute_pair
# --------------------------------------------------------------------------


class TestCompareAttributePair:
    def test_both_absent(self):
        cmp = compare_attribute_pair("building_height_m", None, None)
        assert not cmp.gt_present
        assert not cmp.pdf_present
        assert cmp.is_correct is None

    def test_gt_only(self):
        gt = GroundTruthAttribute(attribute_key="building_height_m", value=10.0)
        cmp = compare_attribute_pair("building_height_m", gt, None)
        assert cmp.gt_present
        assert not cmp.pdf_present
        assert cmp.is_correct is None

    def test_pdf_only(self):
        pdf = CandidateAttribute(attribute_key="building_height_m", value=10.0, confidence=0.9)
        cmp = compare_attribute_pair("building_height_m", None, pdf)
        assert not cmp.gt_present
        assert cmp.pdf_present
        assert cmp.is_correct is None
        assert cmp.pdf_confidence == 0.9

    def test_numeric_within_default_tolerance(self):
        """5% relative tolerance for non-setback numeric attrs."""
        gt = GroundTruthAttribute(attribute_key="gross_floor_area_m2", value=500.0)
        pdf = CandidateAttribute(attribute_key="gross_floor_area_m2", value=524.0)
        cmp = compare_attribute_pair("gross_floor_area_m2", gt, pdf)
        assert cmp.is_numeric
        assert cmp.abs_error == pytest.approx(24.0)
        assert cmp.rel_error == pytest.approx(0.048)
        assert cmp.is_correct is True  # 4.8% < 5%

    def test_numeric_outside_default_tolerance(self):
        gt = GroundTruthAttribute(attribute_key="gross_floor_area_m2", value=500.0)
        pdf = CandidateAttribute(attribute_key="gross_floor_area_m2", value=560.0)
        cmp = compare_attribute_pair("gross_floor_area_m2", gt, pdf)
        assert cmp.is_correct is False  # 12% > 5%

    def test_setback_uses_absolute_tolerance(self):
        """Setbacks have a fixed ±0.2 m absolute tolerance."""
        gt = GroundTruthAttribute(attribute_key="front_setback_m", value=4.5)
        pdf_ok = CandidateAttribute(attribute_key="front_setback_m", value=4.65)
        pdf_fail = CandidateAttribute(attribute_key="front_setback_m", value=4.9)
        assert compare_attribute_pair("front_setback_m", gt, pdf_ok).is_correct is True
        assert compare_attribute_pair("front_setback_m", gt, pdf_fail).is_correct is False

    def test_building_height_absolute_tolerance(self):
        gt = GroundTruthAttribute(attribute_key="building_height_m", value=10.0)
        pdf_ok = CandidateAttribute(attribute_key="building_height_m", value=10.49)
        pdf_fail = CandidateAttribute(attribute_key="building_height_m", value=10.51)
        assert compare_attribute_pair("building_height_m", gt, pdf_ok).is_correct is True
        assert compare_attribute_pair("building_height_m", gt, pdf_fail).is_correct is False

    def test_categorical_exact_match(self):
        gt = GroundTruthAttribute(attribute_key="primary_use_class", value="Residential")
        pdf = CandidateAttribute(attribute_key="primary_use_class", value="residential")
        cmp = compare_attribute_pair("primary_use_class", gt, pdf)
        assert not cmp.is_numeric
        assert cmp.is_correct is True

    def test_categorical_mismatch(self):
        gt = GroundTruthAttribute(attribute_key="primary_use_class", value="commercial")
        pdf = CandidateAttribute(attribute_key="primary_use_class", value="retail")
        cmp = compare_attribute_pair("primary_use_class", gt, pdf)
        assert cmp.is_correct is False

    def test_integer_count_categorical(self):
        """Counts (parking, units) are in _CATEGORICAL_KEYS — exact match."""
        gt = GroundTruthAttribute(attribute_key="residential_unit_count", value=12)
        pdf_ok = CandidateAttribute(attribute_key="residential_unit_count", value=12)
        pdf_fail = CandidateAttribute(attribute_key="residential_unit_count", value=11)
        assert compare_attribute_pair("residential_unit_count", gt, pdf_ok).is_correct is True
        assert compare_attribute_pair("residential_unit_count", gt, pdf_fail).is_correct is False

    def test_numeric_non_parseable_value(self):
        gt = GroundTruthAttribute(attribute_key="building_height_m", value=10.0)
        pdf = CandidateAttribute(attribute_key="building_height_m", value="unknown")
        cmp = compare_attribute_pair("building_height_m", gt, pdf)
        assert cmp.is_correct is False

    def test_pdf_confidence_propagated(self):
        gt = GroundTruthAttribute(attribute_key="building_height_m", value=10.0)
        pdf = CandidateAttribute(attribute_key="building_height_m", value=10.0, confidence=0.77)
        cmp = compare_attribute_pair("building_height_m", gt, pdf)
        assert cmp.pdf_confidence == pytest.approx(0.77)


# --------------------------------------------------------------------------
# build_attribute_stats
# --------------------------------------------------------------------------


class TestBuildAttributeStats:
    def _make_cmp(
        self,
        key: str,
        gt_present: bool,
        pdf_present: bool,
        is_correct: bool | None = None,
        abs_error: float | None = None,
        rel_error: float | None = None,
    ) -> AttributeComparison:
        return AttributeComparison(
            attribute_key=key,
            gt_present=gt_present,
            pdf_present=pdf_present,
            is_correct=is_correct,
            abs_error=abs_error,
            rel_error=rel_error,
        )

    def test_true_positive_counted(self):
        cmps = [self._make_cmp("building_height_m", True, True, True)]
        stats = build_attribute_stats(cmps)
        s = stats["building_height_m"]
        assert s.n_true_positive == 1
        assert s.n_false_positive == 0
        assert s.n_false_negative == 0
        assert s.n_value_correct == 1

    def test_false_positive_counted(self):
        cmps = [self._make_cmp("building_height_m", False, True, None)]
        stats = build_attribute_stats(cmps)
        s = stats["building_height_m"]
        assert s.n_false_positive == 1
        assert s.n_true_positive == 0

    def test_false_negative_counted(self):
        cmps = [self._make_cmp("building_height_m", True, False, None)]
        stats = build_attribute_stats(cmps)
        s = stats["building_height_m"]
        assert s.n_false_negative == 1
        assert s.n_gt_present == 1

    def test_precision_recall_computed(self):
        cmps = [
            self._make_cmp("front_setback_m", True, True, True),   # TP
            self._make_cmp("front_setback_m", True, False, None),   # FN
            self._make_cmp("front_setback_m", False, True, None),   # FP
        ]
        stats = build_attribute_stats(cmps)
        s = stats["front_setback_m"]
        assert s.precision == pytest.approx(0.5)   # TP/(TP+FP) = 1/2
        assert s.recall == pytest.approx(0.5)      # TP/(TP+FN) = 1/2

    def test_error_accumulated(self):
        cmps = [
            self._make_cmp("gross_floor_area_m2", True, True, True, abs_error=10.0, rel_error=0.02),
            self._make_cmp("gross_floor_area_m2", True, True, False, abs_error=40.0, rel_error=0.08),
        ]
        stats = build_attribute_stats(cmps)
        s = stats["gross_floor_area_m2"]
        assert s.abs_errors == [10.0, 40.0]
        assert s.rel_errors == pytest.approx([0.02, 0.08])

    def test_value_accuracy_property(self):
        cmps = [
            self._make_cmp("building_height_m", True, True, True),
            self._make_cmp("building_height_m", True, True, True),
            self._make_cmp("building_height_m", True, True, False),
        ]
        stats = build_attribute_stats(cmps)
        s = stats["building_height_m"]
        assert s.value_accuracy == pytest.approx(2 / 3)

    def test_empty_comparisons(self):
        stats = build_attribute_stats([])
        assert stats == {}


# --------------------------------------------------------------------------
# build_calibration
# --------------------------------------------------------------------------


class TestBuildCalibration:
    def _make_cmp_with_conf(
        self, conf: float, correct: bool
    ) -> AttributeComparison:
        return AttributeComparison(
            attribute_key="building_height_m",
            gt_present=True,
            pdf_present=True,
            pdf_confidence=conf,
            is_correct=correct,
        )

    def test_bucket_count(self):
        buckets = build_calibration([], n_buckets=10)
        assert len(buckets) == 10
        assert buckets[0].conf_low == pytest.approx(0.0)
        assert buckets[-1].conf_high == pytest.approx(1.0)

    def test_correct_bucket_assignment(self):
        cmps = [
            self._make_cmp_with_conf(0.85, True),
            self._make_cmp_with_conf(0.92, False),
        ]
        buckets = build_calibration(cmps, n_buckets=10)
        # conf 0.85 → bucket index 8 ([0.8, 0.9))
        b8 = buckets[8]
        assert b8.n == 1
        assert b8.n_correct == 1
        # conf 0.92 → bucket index 9 ([0.9, 1.0))
        b9 = buckets[9]
        assert b9.n == 1
        assert b9.n_correct == 0

    def test_no_calibration_when_presence_only(self):
        """Comparisons without is_correct (presence mismatch) are skipped."""
        cmp = AttributeComparison(
            attribute_key="building_height_m",
            gt_present=True,
            pdf_present=False,
            pdf_confidence=None,
            is_correct=None,
        )
        buckets = build_calibration([cmp])
        assert all(b.n == 0 for b in buckets)

    def test_observed_accuracy_and_gap(self):
        # All in bucket [0.8, 0.9): 2 correct out of 3.
        cmps = [
            self._make_cmp_with_conf(0.81, True),
            self._make_cmp_with_conf(0.85, True),
            self._make_cmp_with_conf(0.88, False),
        ]
        buckets = build_calibration(cmps, n_buckets=10)
        b8 = buckets[8]
        assert b8.observed_accuracy == pytest.approx(2 / 3)
        # gap = observed - midpoint = 0.667 - 0.85 ≈ -0.183
        assert b8.gap == pytest.approx(2 / 3 - 0.85, abs=1e-9)


# --------------------------------------------------------------------------
# _percentile
# --------------------------------------------------------------------------


class TestPercentile:
    def test_single_element(self):
        assert _percentile([5.0], 50) == 5.0

    def test_median_of_even_list(self):
        assert _percentile([1.0, 2.0, 3.0, 4.0], 50) == pytest.approx(2.5)

    def test_p90(self):
        values = list(range(1, 11))  # 1..10
        p90 = _percentile(values, 90)
        assert 9.0 <= p90 <= 10.0

    def test_empty_returns_nan(self):
        assert math.isnan(_percentile([], 50))


# --------------------------------------------------------------------------
# _parse_llm_json_response
# --------------------------------------------------------------------------


class TestParseLlmJsonResponse:
    def test_parses_clean_json(self):
        raw = '{"building_height_m": 12.5, "building_height_m__confidence": 0.9}'
        attr_map, warnings = _parse_llm_json_response(raw)
        assert "building_height_m" in attr_map
        assert attr_map["building_height_m"].value == pytest.approx(12.5)
        assert attr_map["building_height_m"].confidence == pytest.approx(0.9)
        assert warnings == []

    def test_strips_markdown_fences(self):
        raw = "```json\n{\"primary_use_class\": \"residential\"}\n```"
        attr_map, _ = _parse_llm_json_response(raw)
        assert "primary_use_class" in attr_map

    def test_invalid_json_warns(self):
        raw = "not json"
        attr_map, warnings = _parse_llm_json_response(raw)
        assert attr_map == {}
        assert any("non-JSON" in w for w in warnings)

    def test_null_values_skipped(self):
        raw = '{"building_height_m": null, "gross_floor_area_m2": 300}'
        attr_map, _ = _parse_llm_json_response(raw)
        assert "building_height_m" not in attr_map
        assert "gross_floor_area_m2" in attr_map

    def test_confidence_keys_not_included_as_attributes(self):
        raw = '{"building_height_m": 10, "building_height_m__confidence": 0.8}'
        attr_map, _ = _parse_llm_json_response(raw)
        assert "building_height_m__confidence" not in attr_map

    def test_default_confidence_applied(self):
        raw = '{"building_height_m": 10}'
        attr_map, _ = _parse_llm_json_response(raw)
        # Default confidence is 0.85 when no __confidence key provided.
        assert attr_map["building_height_m"].confidence == pytest.approx(0.85)

    def test_non_object_json_warns(self):
        raw = '[1, 2, 3]'
        attr_map, warnings = _parse_llm_json_response(raw)
        assert attr_map == {}
        assert warnings


# --------------------------------------------------------------------------
# _estimate_llm_cost
# --------------------------------------------------------------------------


def test_estimate_llm_cost_returns_string():
    cost_str = _estimate_llm_cost(10_000, 500)
    assert cost_str.startswith("~$")
    # 10k input @ $0.80/1M + 500 output @ $4.00/1M = 0.008 + 0.002 = ~$0.0100
    cost_val = float(cost_str[2:])
    assert 0.005 < cost_val < 0.02


# --------------------------------------------------------------------------
# Demo mode end-to-end
# --------------------------------------------------------------------------


class TestDemoMode:
    def test_produces_three_projects(self):
        results = _run_demo()
        assert len(results) == 3

    def test_each_project_has_comparisons(self):
        results = _run_demo()
        for r in results:
            assert len(r.comparisons) > 0

    def test_comparisons_structurally_valid(self):
        results = _run_demo()
        for r in results:
            for cmp in r.comparisons:
                assert isinstance(cmp, AttributeComparison)
                assert cmp.attribute_key

    def test_report_builds(self):
        results = _run_demo()
        report = build_report(results)
        assert isinstance(report, EvalReport)
        assert report.per_attribute
        assert len(report.calibration) == 10

    def test_report_renders_markdown(self):
        results = _run_demo()
        report = build_report(results)
        md = render_report_markdown(report)
        assert "# PDF Extraction Accuracy Report" in md
        assert "## Summary" in md
        assert "## Per-Attribute Breakdown" in md
        assert "## Confidence Calibration" in md
        assert "## Release-Blocking Issues" in md
        assert "## Per-Project Results" in md

    def test_report_contains_exit_gate_table(self):
        results = _run_demo()
        report = build_report(results)
        md = render_report_markdown(report)
        assert "Exit Gate Status" in md
        assert "top-10" in md.lower() or "top10" in md.lower() or "Top-10" in md

    def test_high_accuracy_project_detected(self):
        """Project A has good PDF accuracy — precision should be non-zero."""
        results = _run_demo()
        report = build_report(results)
        # building_height_m appears in project A with correct value.
        s = report.per_attribute.get("building_height_m")
        assert s is not None
        assert s.n_true_positive >= 1

    def test_false_negative_captured(self):
        """Project A's parking_stalls_count is in GT but missing from PDF."""
        results = _run_demo()
        report = build_report(results)
        s = report.per_attribute.get("parking_stalls_count")
        assert s is not None
        # FN from project A (PDF missed it); project B has it correct.
        assert s.n_false_negative >= 1

    def test_false_positive_captured(self):
        """Project B hallucinated bicycle_stalls_count not in GT."""
        results = _run_demo()
        report = build_report(results)
        s = report.per_attribute.get("bicycle_stalls_count")
        assert s is not None
        assert s.n_false_positive >= 1


# --------------------------------------------------------------------------
# CLI: demo mode
# --------------------------------------------------------------------------


class TestCLI:
    def test_demo_flag_exit_0_or_1(self, tmp_path):
        """--demo should exit 0 (gate passed) or 1 (gate failed) — not 2."""
        code = main(["--demo", "--out", str(tmp_path)])
        assert code in (0, 1)

    def test_demo_writes_report_file(self, tmp_path):
        main(["--demo", "--out", str(tmp_path)])
        md_files = list(tmp_path.glob("pdf_accuracy_*.md"))
        assert len(md_files) == 1

    def test_demo_report_file_non_empty(self, tmp_path):
        main(["--demo", "--out", str(tmp_path)])
        md_file = next(tmp_path.glob("pdf_accuracy_*.md"))
        content = md_file.read_text(encoding="utf-8")
        assert len(content) > 500

    def test_no_args_exits_2(self):
        with pytest.raises(SystemExit) as exc_info:
            main([])
        assert exc_info.value.code == 2

    def test_print_flag_outputs_to_stdout(self, tmp_path, capsys):
        main(["--demo", "--out", str(tmp_path), "--print"])
        captured = capsys.readouterr()
        assert "# PDF Extraction Accuracy Report" in captured.out


# --------------------------------------------------------------------------
# Taxonomy-level sanity checks
# --------------------------------------------------------------------------


class TestTaxonomySanity:
    def test_categorical_keys_are_strings(self):
        for k in _CATEGORICAL_KEYS:
            assert isinstance(k, str)

    def test_top_10_high_value_all_strings(self):
        for k in TOP_10_HIGH_VALUE:
            assert isinstance(k, str)

    def test_top_10_has_setbacks_and_height_and_gfa(self):
        assert "front_setback_m" in TOP_10_HIGH_VALUE
        assert "building_height_m" in TOP_10_HIGH_VALUE
        assert "gross_floor_area_m2" in TOP_10_HIGH_VALUE

    def test_numeric_tolerance_table_has_setbacks(self):
        for key in ("front_setback_m", "rear_setback_m", "side_setback_left_m"):
            assert key in _NUMERIC_TOL_ABS
            assert _NUMERIC_TOL_ABS[key] == pytest.approx(0.2)
