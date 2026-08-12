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
| Graded by | `scripts/verify_test_prompts.py` | `scripts/verify_golden_cases.py` |
| Written to | `verification/SUMMARY.json` | `verification/GOLDEN_SUMMARY.json` |
| What it gates | nothing — advisory | a production deploy |

The two are never summed, averaged, or reported as one pass rate. A run with
19/20 generated cases passing and an unattested golden subset has demonstrated
nothing about correctness.

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

## Grading a run

```bash
# Validate the file without a run or a database
python scripts/verify_golden_cases.py --check

# Grade a run; writes verification/GOLDEN_SUMMARY.json + TC-NNN.golden.json
python scripts/verify_golden_cases.py evals/runs/<ts> \
  --corpus-json evals/fixtures/abs462_corpus_snapshot.json

# Deploy gate — exit 0 only if every entry is attested and passes
python scripts/verify_golden_cases.py evals/runs/<ts> --gate
```

Selection rationale and the gating decision: [docs/ABS-468-EVAL-GROUND-TRUTH.md](../../docs/ABS-468-EVAL-GROUND-TRUTH.md).
