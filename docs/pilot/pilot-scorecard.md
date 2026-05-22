# Phase-2 pilot scorecard (ABS-54)

The exit criteria the [pilot runbook](halifax-pilot-runbook.md) feeds
into. Each pilot project produces a row in the scorecard; Phase 2
"succeeds" when all three projects hit every threshold. Anything
short of that converts to a sized follow-up issue.

The thresholds below come straight from the issue spec — don't ease
them as the pilot runs. If the system can't hit them, the right move
is more iteration, not a softer bar.

## Quantitative thresholds

| Metric | Threshold | How it's measured | Where the data lives |
|---|---|---|---|
| Setback accuracy | ≥ 80% of `*_setback_m` measurements within ±0.2 m of ground truth | `scripts/pilot_variance_report.py` output per CSV | `docs/pilots/data/pilot_<customer>_<project>.csv` |
| Categorical accuracy | ≥ 90% of `primary_use_class`, `*_count`, `*_boolean` attrs correct, per project | Manual operator review against the CSV | Same CSV; `notes` column captures the misses |
| Evaluator verdict accuracy | ≥ 75% of (attribute × clause) pairs match the customer's professional assessment | Customer marks each matrix row "agree / disagree / unsure" in the weekly review | Captured in the per-project write-up (qualitative table) |
| Customer NPS-style verdict | "I would pay for this" or equivalent at end of pilot | Direct quote captured in the write-up | `docs/pilots/phase2_pilot_<customer>.md` |

## Per-project scorecard template

Use this Markdown table once per project, append to the customer's
write-up. Numbers come from the variance report; verdicts come from
the weekly review.

```markdown
### Project: <project-slug>

| Metric | Target | Result | Pass? |
|---|---|---|---|
| Setbacks within ±0.2 m | ≥ 80% | __% (n/4) | ✅ / ❌ |
| Categorical attrs correct | ≥ 90% | __% (n/total) | ✅ / ❌ |
| Evaluator verdicts agreed | ≥ 75% | __% (n/total) | ✅ / ❌ |
| Customer overall verdict | "I would pay for this" | (quote) | ✅ / ❌ |
```

## Aggregate exit gate

Phase 2 closes successfully iff:

1. All 3 projects passed all 4 thresholds, AND
2. The customer signed the post-pilot conversion (paid plan, or
   formal letter-of-intent), AND
3. Every follow-up issue surfaced during the pilot was filed in
   Linear under the appropriate phase project, with the customer's
   exact quote as the issue description (so the next engineer
   doesn't have to interpret).

Anything short of all three converts to:

- A "Phase 2 — pilot blockers" project rollup in Linear.
- A short retrospective document at
  `docs/pilots/phase2_pilot_<customer>_lessons.md` covering what
  didn't work, what we should have known earlier, and what we change
  in the pilot runbook for the next attempt.

## What we're NOT measuring (and why)

Listed so future operators don't bolt these on under deadline pressure:

- **Compute cost per submission.** Phase-2 pipeline is sub-second
  Python; cost is rounding error. Worth measuring at Phase-3 scale
  (PDF + APS), not here.
- **Latency end-to-end.** Upload + extract is < 5 seconds on a
  typical IFC; if the customer complains, that's already captured
  via the weekly review's "anything that confused you" question.
- **Multi-user / collaboration.** No customer in the pilot will hand
  the tool to their team during the pilot window. Multi-user UX is
  Phase 3 if the pilot validates demand.
