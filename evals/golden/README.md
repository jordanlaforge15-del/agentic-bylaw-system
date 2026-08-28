# The golden subset — human-validated ground truth

`golden_cases.json` is the only set of expectations in this project that does
not originate from a model. Everything in
`evals/regional_centre_test_prompts.json` — the questions, the personas, the
expected keywords, the expected answers — was authored by `claude -p`, and the
system under test is a Claude model. A generated case that passes establishes
that the advisor agrees with what a model guessed the answer was. It does not
establish that the answer is correct under the by-law (ABS-468).

This directory exists so the two kinds of evidence cannot be confused.

| | Generated cases | Golden subset |
|---|---|---|
| Authored by | `claude -p` | a qualified human |
| Lives in | `evals/regional_centre_test_prompts.json` | `evals/golden/golden_cases.json` |
| Graded by | `scripts/verify_test_prompts.py` (internal) | `scripts/verify_golden_cases.py` (internal) |
| Written to | `verification/SUMMARY.json` | `verification/GOLDEN_SUMMARY.json` |
| What it gates | nothing — advisory | a production deploy |

The two are never summed, averaged, or reported as one pass rate. A run with
19/20 generated cases passing and an unattested golden subset has demonstrated
nothing about correctness.

**Both graders run from one command** — `python scripts/verify_run.py <run_dir>`
(ABS-516). Two entry points meant a caller could run the advisory one, read
"0 FAIL", and report the run as passing; that is exactly what happened in
the `zone-typology-all8` run on the
`docs/zone-typology-test-questions` branch. Separation of *evidence* is the point here;
separation of *entry points* was the bug. See [Grading a run](#grading-a-run).

## Filling in an attestation

Each entry is unattested. The `attestation` block is the reviewer's; nothing
else in the entry should change.

**Who.** Someone qualified to give the answer professionally — the pilot's
cooperative architect/developer, a planner, or a municipal reviewer. Not an
engineer on this project, and not a model. If a model drafted anything that
lands in these fields, the entry is not attested; the whole point of the
artifact is defeated.

**How.** Answer `question_for_reviewer` from the by-law, then express the answer
in the three machine-checkable fields below. Keep `correct_answer` in prose —
it is what a future reader will actually rely on; the other fields exist so a
script can grade against it.

```json
"attestation": {
  "status": "attested",
  "attested_by": {
    "name": "A. Reviewer",
    "credential": "MCIP, LPP",
    "affiliation": "…"
  },
  "attested_on": "2026-08-20",
  "method": "Read the Regional Centre LUB provisions listed below against the facts in the case. No model involved.",
  "correct_answer": "Prose. What a competent professional would tell this person, including anything the by-law does not settle.",
  "governing_provisions": [
    {
      "reference": "Section 198(1)(f)",
      "holding": "2.5 m side setback applies; the 0.0 m clause is conditional on abutting DD/DH/CEN/COR land."
    }
  ],
  "must_state": [
    {
      "id": "side-setback-2.5m",
      "description": "Gives 2.5 m as the governing side setback",
      "any_of": ["2.5 m", "2.5 metres"]
    }
  ],
  "must_not_state": [
    {
      "id": "no-zero-side-setback",
      "description": "Must not tell this owner the side setback is zero",
      "any_of": ["0.0 m side", "no side setback"]
    }
  ],
  "reviewer_notes": "Anything the fields above flatten."
}
```

### What the three fields mean to the grader

- **`governing_provisions`** — every reference listed must be cited by the
  answer. List only the provisions that genuinely govern; a provision that is
  merely interesting weakens the check by turning a real miss into noise. Each
  is also resolved against the ingested corpus, so a reference that does not
  exist in the by-law is reported rather than silently counted.
- **`must_state`** — propositions the answer is wrong without. A group hits if
  **any** of its `any_of` phrases appears anywhere in the conversation
  (case-insensitive). Give the phrasings a correct answer would plausibly use;
  the grader does not paraphrase.
- **`must_not_state`** — propositions that make the answer wrong. Any hit fails
  the case outright. This is the field that catches the confident-wrong answer,
  and it is the one a generated eval cannot produce, because a model does not
  know which wrong answer it is inclined to give.

### `answer_shape`

- `determinate` — one right answer.
- `depends` — the correct answer is conditional on facts outside the by-law
  (a precinct map, a negotiated agreement, a site-specific finding). The grader
  additionally requires the answer to be conditional.
- `refusal` — the correct answer is substantially that the by-law does not
  settle the question. The grader additionally requires the answer to say so.

The last two exist because an eval that only contains determinate cases rewards
confidence, and confidence is the failure mode with the highest cost here.

### Heading consistency — a check with no attestation (ABS-519)

One check grades the answer against **itself**, so it applies to every case
without a reviewer writing a rule: **no section heading may assert the opposite
of its own body.**

The defect that prompted it (TC-026) was a *correct* refusal under the heading
`### 1. Townhouse Dwelling Use — Permitted in ER-2 (with conditions)`, above a
paragraph explaining the use is permitted in ER-3 and **not** in ER-2. Body
right, heading wrong — and the heading is what a skimming reader acts on.

It could not be written as a `must_not_state` phrase. Every phrasing that
catches the heading also catches the correct sentence: `"permitted in ER-2"` is
a substring of `"is not permitted in ER-2"`, so the rule would fail a right
answer, and `"is permitted in ER-2"` misses the bare heading form
`"— Permitted in ER-2"`. Polarity is structural, not lexical, so the grader
reads structure — headings, their sections, and the clause-level polarity of
each claim (`advisor.chat.heading_consistency`, shared with the generation-time
guard so grader and product agree on what a contradiction is).

A contradiction is a `GOLDEN_FAIL`, with the offending heading, the zone, and a
non-contradicting rewrite recorded under `heading_consistency` in the case's
`.golden.json`.

**Limits, so nobody reads a pass as broader than it is:**

- Only ATX (`#`) headings are examined. A bolded pseudo-heading on its own line
  is not treated as a heading.
- Claims are anchored on **zone codes** (`ER-2`, `CEN-2`, `HR-1`). A heading
  whose claim hangs on something else (a use, an overlay, a lot) is compared
  only when its section discusses at most one zone; a multi-zone section under
  an unanchored heading is skipped as genuinely ambiguous rather than guessed
  at.
- The permission vocabulary is `permitted` / `allowed` / `permissible` /
  `prohibited` / `disallowed` / `forbidden` / `impermissible`. A contradiction
  phrased entirely outside it (a heading saying "You can build four
  townhouses") is not caught. Extend the word lists in the module — not with a
  per-case phrase — when a run turns one up.
- A permission word qualifying a noun — `Permitted Uses in ER-2`,
  `prohibited structures` — names a **topic**, not a verdict, and is ignored,
  so a legitimate "Permitted Uses in ER-2" heading is never rewritten over a
  body that denies one particular use. The cost is a blind spot the other way:
  `"townhouses are a prohibited use in ER-2"` reads as a topic too, so a
  heading contradicting only that sentence goes unflagged. Negated forms are
  exempt — `"is not a permitted use in ER-2"` is only ever a verdict.
- Headings are graded **per turn**. A heading in turn 1 does not introduce
  turn 2's prose.

## Grading a run

**One command grades a run.** It prints the gating tier first, the advisory tier
second, never adds them, and takes its exit status from the golden tier alone:

```bash
python scripts/verify_run.py evals/runs/<ts>

# …or offline, against a committed corpus slice instead of the dev DB:
python scripts/verify_run.py evals/runs/<ts> \
  --corpus-json evals/fixtures/abs462_corpus_snapshot.json
```

```
GOLDEN (human-attested, gates deploy)     3 PASS  1 PARTIAL  4 FAIL   [GATE: CLOSED]
GENERATED (model-authored, advisory)      5 PASS  3 PARTIAL  0 FAIL   [gates nothing]
```

Exit `0` = the deploy gate is open, `1` = closed, `2` = the run could not be
graded (bad path, malformed golden file). A perfect advisory sweep cannot open
the gate and a failing one cannot close it. Artifacts: `GOLDEN_SUMMARY.json`,
`SUMMARY.json`, `TC-NNN.golden.json`, `TC-NNN.verify.json`, and
`RUN_SUMMARY.json` — which keeps the two tiers under separate keys with no
total.

The two graders remain runnable on their own for iterating on a grader; neither
answers "did this run pass?", and `verify_test_prompts.py` prints a banner
saying so.

```bash
# Validate the golden file — no run, no database
python scripts/verify_golden_cases.py --check

# The gating tier alone (verify_run.py runs this for you)
python scripts/verify_golden_cases.py evals/runs/<ts> --gate
```

## Where the gate is enforced (ABS-485)

`verify_run.py` answers "did *this run* pass?" — it needs a run, and a run costs
metered API spend. A promotion pipeline needs a cheaper question answered:
**may this release be promoted?**

```bash
python scripts/check_deploy_gate.py          # 0 open, 1 held, 2 could not evaluate
python scripts/check_deploy_gate.py --json   # machine-readable
```

No run, no database, no API spend — the condition that holds the gate today
(is every entry attested?) is a file check. It reports `unattested`,
`no_graded_run` and `graded_failing` separately, because a hold and a failure
demand opposite responses: one is "a qualified human has work to do", the other
is "the advisor is wrong".

Once attested, an attestation nothing has graded still proves nothing, so the
gate additionally requires a `GOLDEN_SUMMARY.json` whose recorded
`golden_file_sha256` matches this file's bytes. That digest is what stops
*attest → grade → green → edit an attestation → promote* from reading as gated:
the stale summary graded a different file.

Three places run it, independently, so skipping one does not ship:

| Where | What a held gate does |
|---|---|
| `.claude/skills/test-and-deploy-bylaw/SKILL.md` Step 7.0 | Halts before `dev → main` is merged or tagged. |
| `.claude/skills/deploy-bylaw/SKILL.md` preconditions | Halts before any image is built, for a direct deploy. |
| `golden-gate` job in `.github/workflows/ci.yml` | Fails on `main` and blocks both image builds via `needs`. Warns without failing on `dev` — feature work is not a production deploy. |

**The gate is held today and will stay held until a human attests.** That is
the artifact working, not a pipeline to route around. The only way to open it is
[Filling in an attestation](#filling-in-an-attestation), by someone qualified to
give the answer professionally. Backfilling one — as a placeholder, to unblock a
release, or from a careful reading of the by-law — turns the project's only
non-model ground truth into a record of what the model already says.

## One run is not a verdict (ABS-524)

A case that grades PASS on one run and PARTIAL on the next has told you
nothing yet. TC-022 did exactly that: five recorded transcripts, three citing
Table 1B and two stating the same permission bare, over an evidence channel
that was **byte-identical** across all five. The retrieval payload did not
change; the answer's layout did.

That matters because the first read of the two failures blamed a code change
that had landed hours earlier — a permission-grid backfill that never touched
the cell in question. Fisher's exact on the pre/post partition came out at
p = 0.40. A single-run comparison would have shipped a revert for a defect
that predated it by 34 hours.

So, before a case's verdict drives work:

- **Don't diff two single runs.** A flip between adjacent runs is the null
  hypothesis, not a signal. Establish the rate on each side first.
- **N ≥ 5 per side** before calling a case fixed or regressed, and say the N
  out loud when reporting it.
- **Diff the evidence, not just the verdicts.** The transcripts carry the tool
  payloads (ABS-517). If the payload backing the failing claim is identical to
  the passing run's, the defect is in synthesis and no retrieval change will
  move it.
- **Prefer a deterministic guard to a rerun.** Where a property can be checked
  in the payload or after generation — the ABS-519 heading rule,
  ABS-524's `cite_as` binding — that check runs every time and does not need
  a sample size. Reruns cost metered API spend; a guard costs nothing.

Selection rationale and the gating decision: [docs/ABS-468-EVAL-GROUND-TRUTH.md](../../docs/ABS-468-EVAL-GROUND-TRUTH.md).
