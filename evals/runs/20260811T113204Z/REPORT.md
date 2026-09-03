# ABS-260 — Production-Readiness Sweep Report

**Run directory:** `/Users/christopherrafuse/dev/agentic-bylaw-system/.claude/worktrees/nm-abs-468/evals/runs/20260811T113204Z`
**Generated-case verdict (advisory):** **NO-GO**

> The verdict above is computed against `regional_centre_test_prompts.json`, whose expected answers were authored by `claude -p` — a model of the family under test. It measures agreement with a model's guess, not correctness under the by-law, and it gates nothing. The blocking verdict is the golden subset below (ABS-468).

## Golden subset — human-validated (gating)

**Gate (production_deploy):** **CLOSED**
- unattested: TC-001, TC-002, TC-009, TC-012, TC-008, TC-014

| Case | Verdict | Zone | Liability | Answer shape | Reasons |
|---|---|---|---|---|---|
| TC-001 | ⬜ UNATTESTED | HR-1 | low | determinate | no qualified human has recorded the correct answer for this case; it cannot pass and it holds the deploy gate closed |
| TC-002 | ⬜ UNATTESTED | ER-2 | low | determinate | no qualified human has recorded the correct answer for this case; it cannot pass and it holds the deploy gate closed |
| TC-009 | ⬜ UNATTESTED | DD | medium | determinate | no qualified human has recorded the correct answer for this case; it cannot pass and it holds the deploy gate closed |
| TC-012 | ⬜ UNATTESTED | RPK | medium | refusal | no qualified human has recorded the correct answer for this case; it cannot pass and it holds the deploy gate closed |
| TC-008 | ⬜ UNATTESTED | DH | high | depends | no qualified human has recorded the correct answer for this case; it cannot pass and it holds the deploy gate closed |
| TC-014 | ⬜ UNATTESTED | CEN-2 | high | depends | no qualified human has recorded the correct answer for this case; it cannot pass and it holds the deploy gate closed |

These counts are never added to the generated-case counts above. A golden case tests whether the advisor is right; a generated case tests whether it agrees with a model.

## Threshold check (generated cases)

- simple PASS rate: 0% (target 100%)
- medium PASS rate: 0% (target ≥85%)
- complex PASS rate: 0% (target ≥85%)
- simple cases below 100% bar

## Headline metrics (generated cases — advisory)

- **Cases run:** 1
- **PASS:** 0   **PARTIAL:** 0   **FAIL:** 0
- **Citations cross-checked:** 7 / 7 grounded in source (100%)
- **Hallucinated citations:** 0

## Per-case verdicts (generated cases — advisory)

| ID | Verdict | Zone | Complexity | Liability | Kw rate | Cites (found/total) | Notes |
|---|---|---|---|---|---|---|---|
| TC-001 | ? FAIL_APPLICABILITY | ER-1 | simple | low | 100% | 7/7 | inapplicable citation: 198(1)(d) applies only where a lot line abuts CEN-1, CEN-2, COR, DD, DH land; the answer never establishes any of those zones |

## Hallucinated citations

**None.** Every citation the advisor produced resolved to a real fragment in the source bylaw.

## Hedging failures (high-liability turns)

**None.** Every high-liability turn included appropriate hedging language.

## Most-frequent expected-keyword misses

All expected keywords were surfaced.

## Per-case transcripts and verifications

- **TC-001** — `TC-001.json` (transcript) · `verification/TC-001.verify.json` (graded)
