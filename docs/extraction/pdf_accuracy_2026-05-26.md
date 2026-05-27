# PDF Extraction Accuracy Report — 2026-05-27

_Generated 2026-05-27 01:43 UTC_

## Summary

| Metric | Value |
|--------|-------|
| Projects evaluated | 3 |
| Attribute comparisons | 16 |
| Overall precision (presence) | 90.0% |
| Overall recall (presence) | 60.0% |
| Overall value accuracy | 77.8% |

## Exit Gate Status (Phase 3 PDF Track)

| Gate | Target | Actual | Status |
|------|--------|--------|--------|
| Precision on top-10 high-value attrs | ≥80% | 100.0% | ✅ PASS |

## Per-Attribute Breakdown

| Attribute | Prec | Recall | Val Acc | GT Present | PDF Present | Mean |Err| | p50 |Err| | p90 |Err| |
|-----------|------|--------|---------|-----------|------------|------------|------------|------------|
| bicycle_stalls_count | 0.0% | n/a | n/a | 0 | 1 | — | — | — |
| building_height_m | 100.0% | 66.7% | 50.0% | 3 | 2 | 0.500 | 0.500 | 0.740 |
| building_height_storeys | 100.0% | 100.0% | 100.0% | 1 | 1 | — | — | — |
| front_setback_m | 100.0% | 100.0% | 100.0% | 1 | 1 | 0.100 | 0.100 | 0.100 |
| gross_floor_area_m2 | 100.0% | 33.3% | 100.0% | 3 | 1 | 3.000 | 3.000 | 3.000 |
| parking_stalls_count | 100.0% | 50.0% | 100.0% | 2 | 1 | — | — | — |
| primary_use_class | 100.0% | 66.7% | 50.0% | 3 | 2 | — | — | — |
| rear_setback_m | n/a | 0.0% | n/a | 1 | 0 | — | — | — |
| residential_unit_count | 100.0% | 100.0% | 100.0% | 1 | 1 | — | — | — |

## Confidence Calibration

_A well-calibrated extractor has ~0 gap (confidence ≈ observed accuracy)._

| Conf. bucket | N | Observed acc | Gap |
|--------------|---|--------------|-----|
| [0.6, 0.7) | 1 | 0.0% | -65.0pp |
| [0.7, 0.8) | 1 | 0.0% | -75.0pp |
| [0.8, 0.9) | 4 | 100.0% | +15.0pp |
| [0.9, 1.0) | 3 | 100.0% | +5.0pp |

## Release-Blocking Issues

_Attributes where accuracy is too poor for production use even with human confirmation._

None identified at this time (insufficient data or all above threshold).

## Per-Project Results

### Alpha Residential Tower (`proj-a-residential`)

- **Complexity:** high
- **IFC extraction:** 0.1s
- **PDF extraction:** 12.4s
- **PDF pages:** 48
- **Time per page:** 0.3s
- **LLM tokens (in/out):** 18500 / 420
- **Estimated LLM cost:** ~$0.0165
- **Precision:** 100.0% | **Recall:** 83.3%

### Beta Commercial Block (`proj-b-commercial`)

- **Complexity:** medium
- **IFC extraction:** 0.1s
- **PDF extraction:** 6.8s
- **PDF pages:** 24
- **Time per page:** 0.3s
- **LLM tokens (in/out):** 9200 / 310
- **Estimated LLM cost:** ~$0.0086
- **Precision:** 80.0% | **Recall:** 66.7%

### Gamma Scan-Only Set (`proj-c-scan`)

- **Complexity:** low
- **IFC extraction:** 0.1s
- **PDF extraction:** 3.2s
- **PDF pages:** 12
- **Time per page:** 0.3s
- **LLM tokens (in/out):** 4100 / 120
- **Estimated LLM cost:** ~$0.0038
- **Precision:** n/a | **Recall:** 0.0%

## Methodology

- **Ground truth:** IFC extractor (`layer1.parsers.ifc_submission`) — direct Pset reads at confidence 1.0, derived paths at 0.6.
- **PDF candidate:** Claude LLM extraction from PDF text (via PdfParser).
- **Numeric correctness:** within absolute tolerance (setbacks ±0.2 m, height ±0.5 m) or 5% relative.
- **Categorical correctness:** exact string match (lowercased).
- **Confidence calibration:** compares reported PDF confidence against observed value accuracy per 0.1-wide bucket.
