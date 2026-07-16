# ABS-260 — Production-Readiness Sweep Report

**Run directory:** `/Users/christopherrafuse/dev/agentic-bylaw-system/evals/runs/20260603T092804Z`
**Overall verdict:** **NO-GO**

## Threshold check

- simple PASS rate: 0% (target 100%)
- medium PASS rate: 0% (target ≥85%)
- complex PASS rate: 100% (target ≥85%)
- simple cases below 100% bar

## Headline metrics

- **Cases run:** 1
- **PASS:** 1   **PARTIAL:** 0   **FAIL:** 0
- **Citations cross-checked:** 62 / 62 grounded in source (100%)
- **Hallucinated citations:** 0

## Per-case verdicts

| ID | Verdict | Zone | Complexity | Liability | Kw rate | Cites (found/total) | Notes |
|---|---|---|---|---|---|---|---|
| TC-005 | ✅ PASS | HR-2 | complex | high | 63% | 62/62 |  |

## Hallucinated citations

**None.** Every citation the advisor produced resolved to a real fragment in the source bylaw.

## Hedging failures (high-liability turns)

**None.** Every high-liability turn included appropriate hedging language.

## Most-frequent expected-keyword misses

These spec-expected keywords appeared in the test prompts' `expected_answer_keywords` but NOT in the advisor's response. High counts may indicate either advisor gaps OR overly-rigid test expectations (e.g. spatial lookup overrode the test's assumed zone).

| Keyword | Miss count |
|---|---|
| `permitted` | 3 |
| `dwelling unit` | 3 |
| `home occupation` | 3 |
| `multi-unit dwelling` | 2 |

## Per-case transcripts and verifications

- **TC-005** — `TC-005.json` (transcript) · `verification/TC-005.verify.json` (graded)
