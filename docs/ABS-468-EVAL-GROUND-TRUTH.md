# ABS-468 — Two tiers of eval evidence, and which one gates a deploy

## The problem

`scripts/generate_regional_centre_test_prompts.py` generates each case in
`evals/regional_centre_test_prompts.json` — the question, the persona, the
expected keywords, the expected references, the expected topics — via
`claude -p`. The system under test is a Claude model.

So a passing case establishes that the advisor agrees with what a Claude model
guessed the answer was. It does not establish that the answer is correct under
the by-law. That is the failure mode an eval exists to catch, and it was inside
the eval itself.

Prior work narrowed the gap without closing it:

| Field | Grounding before ABS-468 |
| -- | -- |
| `expected_bylaw_references` | Validated against the corpus (ABS-463) |
| `expected_answer_keywords` | Partially — recalibrated against the real ingest (ABS-265) |
| Cited-clause applicability | Validated against the by-law's own trigger conditions (ABS-462) |
| `zone` / `address` | Derived from the zoning schedule and verified (ABS-467) |
| The questions themselves | LLM-invented |
| Whether the expected answer is legally correct | Never established by anyone qualified |

ABS-462 is the closest thing to independent grading in the project: asking
whether a cited clause *applies* is grounded in the by-law rather than in a
model's opinion. But it can only assess the citations an answer happens to
make. It cannot tell you the question was worth asking, or that the correct
answer was something the advisor never mentioned.

## What now exists

Two tiers, in separate files, graded by separate scripts, written to separate
artifacts, using disjoint verdict vocabularies.

| | Generated | Golden |
| -- | -- | -- |
| Cases | `evals/regional_centre_test_prompts.json` (20) | `evals/golden/golden_cases.json` (6) |
| Authored by | `claude -p` | a qualified human |
| Graded by | `scripts/verify_test_prompts.py` (internal) | `scripts/verify_golden_cases.py` (internal) |
| Entry point | `scripts/verify_run.py` (ABS-516) | the same command, golden printed first |
| Artifact | `verification/SUMMARY.json` | `verification/GOLDEN_SUMMARY.json` |
| Verdicts | `PASS` / `PARTIAL` / `FAIL*` / `NO_DATA` | `GOLDEN_PASS` / `GOLDEN_PARTIAL` / `GOLDEN_FAIL` / `UNATTESTED` / `NO_TRANSCRIPT` |
| Gates | nothing | a production deploy |

Every generated summary row now carries `evidence_tier: "generated"`, and
`REPORT.md` labels the ABS-260 threshold verdict *advisory* and renders the
golden gate as its own block. The two counts are never summed. The verdict
strings do not overlap, so a script reading both cannot merge them by accident
either — `tests/scripts/test_verify_golden_cases.py` pins that.

## Case selection and why

Six cases. The constraints were a zone spread, a liability spread, and at least
one case where the correct answer is a refusal or a "depends" — the last being
the important one. A subset of determinate dimensional lookups is cheap to
attest and measures almost nothing: the answers most likely to be wrong, and
most expensive when wrong, are the ones where the by-law does not give a
number. An eval made only of questions with clean answers rewards confidence,
which is the failure mode with the highest cost here.

| Case | Zone | Liability | Answer shape | Why it is in the subset |
| -- | -- | -- | -- | -- |
| TC-001 | HR-1 | low | determinate | Floor of the range, and the only adversarial premise: the user asserts ER-1, a zone the by-law defines but the schedule maps nowhere. A one-answer dimensional lookup whose real failure mode — answering the question as asked — a keyword grader cannot see. |
| TC-002 | ER-2 | low | determinate | The other half of what the advisor does: use permission rather than dimension. Secondary suite plus a concurrent home business exercises combination-of-uses, where a table lookup alone is wrong. |
| TC-009 | DD | medium | determinate | A wrong answer is acted on immediately — a lawyer clearing a site for acquisition. Turn 2 asks the advisor to confirm the vendor's claim, the shape of question most likely to be answered agreeably rather than correctly. |
| TC-012 | RPK | medium | **refusal** | RPK is in neither Table 1A nor 1B, and the underlying dispute is a boundary encroachment the land use by-law does not resolve. The correct answer is substantially "the by-law does not settle this", which no keyword or citation metric rewards and a fluent wrong answer always beats. |
| TC-008 | DH | high | **depends** | Top of the range, and staff advice repeated to a third party. Turn 3 asks outright whether 90 m is evaluable from the by-law text or depends on the precinct map; an unconditional number is a failure the generated grader scores as a pass. |
| TC-014 | CEN-2 | high | **depends** | Adversarial: the user pushes for a maximum-FAR stack and assumes bonuses are additive. Bonus density is negotiated, so the correct answer is conditional and partly a refusal to quantify — and a model authoring its own expectation here will produce the confident number the user asked for. |

Six zones, no repeats. Two low / two medium / two high. Three determinate, one
refusal, two depends. Personas span homeowner, lawyer, municipal reviewer and
developer.

Coverage is deliberately partial: 6 of 20 cases, 6 of the 25 zones the schedule
maps. The subset is not a sample of the eval, it is a set of cases chosen
because a professional's hour spent on them buys the most.

## The artifact is unattested, on purpose

Every entry in `golden_cases.json` ships with `attestation.status:
"unattested"`. No engineer on this project and no model has filled in a correct
answer, because either would recreate exactly the defect this issue is about —
and the resulting file would be *worse* than the generated eval, since it would
carry the authority of a human-validated artifact while containing none of it.

The mechanism is complete and tested; the content needs the pilot's cooperative
architect/developer (or an equivalent professional) to sit down with the six
`question_for_reviewer` prompts. The intake instructions are in
[evals/golden/README.md](../evals/golden/README.md).

An unattested entry grades `UNATTESTED`. It can never count as a pass, and it
holds the gate closed. The half-filled state — a populated answer with the
status left at `unattested` — is rejected by validation, because it reads as
ground truth and grades as nothing.

## What the golden subset gates (decision)

**The golden subset blocks a production deploy. Generated cases stay advisory
and gate nothing.**

Concretely:

```bash
python scripts/verify_run.py evals/runs/<ts>   # exit 1 = do not ship
```

ABS-516 folded the two graders behind that one command: it prints the golden
tier first, the generated tier second, and its exit status is the golden gate's
alone. `verify_golden_cases.py … --gate` still exists and still gates, but a
caller who runs only `verify_test_prompts.py` now gets a banner telling them
they have not graded the run.

The gate is open only when **every** entry is attested and grades
`GOLDEN_PASS`. `GOLDEN_PARTIAL` — right answer, missing authority — does not
open it: an answer that reaches the right number without the governing
provision is not something to put in front of a professional who will be asked
where it came from. A run with no `GOLDEN_SUMMARY.json` leaves the gate closed,
since nothing in that run speaks to correctness at all.

Today that means the gate is closed and a production deploy of the advisor is
not backed by any independent evidence of correctness. That is the true state
of affairs, and it was equally true before this issue — the difference is that
it is now visible in `REPORT.md` instead of being obscured by a generated pass
rate.

**Operator procedure until the subset is attested.** Run the gate command as a
precondition of any advisor-scope deploy. If it is closed *because the subset is
unattested*, shipping is allowed only on an explicit decision recorded in the
deploy summary — "deployed without independent correctness evidence" — rather
than passed over silently. If it is closed because an **attested** case grades
`GOLDEN_FAIL`, halt: that is the advisor getting a human-checked question wrong,
and no override applies. This belongs in the preconditions of
`.claude/skills/deploy-bylaw/SKILL.md`; that file was not editable from this
worktree, so adding it there is a one-line follow-up.

Not gating on generated cases is a deliberate choice rather than leniency. A
gate on a metric that does not measure correctness is worse than no gate: it
manufactures confidence and it creates pressure to tune the advisor toward
whatever a model expected.

## Relationship to the pilot scorecard

[docs/pilot/pilot-scorecard.md](pilot/pilot-scorecard.md) already contemplates
this shape — "≥ 75% of (attribute × clause) pairs match the customer's
professional assessment". That threshold and this subset measure the same
thing from opposite ends: the scorecard samples a professional's judgement over
live pilot output, the golden subset freezes a professional's judgement into a
regression test. The scorecard's ≥75% agreement bar is a per-pilot exit
criterion; the golden gate is per-deploy and demands 100%, because six cases
chosen for their difficulty is a small enough set that one failure is a real
signal rather than sampling noise.

## Known limits

- **Six cases is not coverage.** Passing the golden subset means the advisor is
  right about six questions a professional checked. It is a floor, not a
  warrant.
- **The grader is still rule-based.** `must_state` / `must_not_state` are
  phrase groups; an answer can satisfy every phrase and still be wrong in a way
  the reviewer did not anticipate. What is new is *whose* anticipation it is.
- **Attestations go stale.** They are pinned to a by-law version. An amendment
  to the Regional Centre LUB invalidates them, and there is no automatic
  detection of that — re-attestation is a manual step after a re-ingest.
- **No LLM-judge stage.** Still deliberately absent; see the "Known limits of a
  rule-based scorer" section in `scripts/verify_test_prompts.py`. A judge would
  be a third tier and would carry the same provenance problem as tier one.
