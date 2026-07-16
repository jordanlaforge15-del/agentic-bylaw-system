// Functional: ABS-371 — the Legal non-conforming determination ($199)
// completes and captures for a routine input.
//
// The bug (sibling of ABS-360 / ABS-370):
//   After ABS-370, legal_nonconforming was the only grounding-heavy
//   catalog question still riding the flat 165k default cumulative
//   reasoning budget. No historical transcripts existed for the SKU, so
//   one representative case was run live with the ABS-305 estimator
//   instrumented in place: a routine corner-store determination grounded
//   in 10 iterations at 176.0k billed-equivalent tokens — past the 165k
//   default outright, i.e. the default voids a routine input on
//   cost_ceiling before any nondeterministic drift is even applied.
//
// The fix:
//   The catalog question now carries the same 260k cumulative ceiling as
//   the other three grounding-heavy SKUs (matching the off-menu
//   "complex" tier). See src/advisor/billing/questions.py and the
//   threading in advisor.billing.answers._resolve_run_inputs.
//
// What this spec guards:
//   The purchase runs end-to-end through the REAL buy-an-answer service
//   (checkout → webhook authorize → run → settle) over the e2e FastAPI +
//   Postgres + MockGateway stack and CAPTURES a grounded answer — the
//   report path that was hard-failing is exercised whole.
//
//   The budget ARITHMETIC (that 260k grounds a load which the 165k
//   default would void) is a token-heuristic property the MockGateway
//   can't reproduce faithfully end-to-end, so it is pinned in the Python
//   unit test tests/advisor/billing/test_buy_answer.py
//   ::test_grounding_heavy_skus_ground_where_the_default_would_void —
//   the same split as abs360/abs370 (heavy-budget math in Python, path
//   guarded end-to-end here). Reverting the budget bump turns that
//   Python test red.

import { expect, test } from "@playwright/test";

import { E2E_API_URL } from "../fixtures/test-env";

function uniqueUser(): string {
  return `abs371-lnc-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`;
}

// Routine input mirroring the live measurement run (6184 Quinpool Rd,
// CEN-2 — the same address ABS-370's due-diligence evidence used).
const INPUTS = {
  address: "6184 Quinpool Rd, Halifax",
  existing_use_or_structure:
    "a corner grocery store occupying the ground floor of a residential building",
  establishment_date: "approximately 1978",
};

test("the $199 legal non-conforming determination completes and captures for a routine input", async ({
  request,
}) => {
  const userId = uniqueUser();

  const checkoutRes = await request.post(
    `${E2E_API_URL}/v1/_test/buy-answer/checkout`,
    {
      data: {
        user_id: userId,
        question_slug: "legal_nonconforming",
        inputs: INPUTS,
      },
    },
  );
  expect(checkoutRes.status(), await checkoutRes.text()).toBe(200);
  const created = await checkoutRes.json();
  expect(created.status).toBe("authorized");
  expect(created.question_slug).toBe("legal_nonconforming");

  const answerRes = await request.post(
    `${E2E_API_URL}/v1/_test/buy-answer/answer`,
    { data: { purchase_id: created.purchase_id } },
  );
  expect(answerRes.status(), await answerRes.text()).toBe(200);
  const answered = await answerRes.json();

  // The paid deliverable produced a grounded determination — NOT the
  // "ran past its reasoning budget" (cost_ceiling) hard failure.
  expect(
    answered.status,
    `expected captured but got ${answered.status} ` +
      `(failure_reason=${answered.failure_reason}) — the ` +
      "legal_nonconforming budget fix (ABS-371) may have regressed",
  ).toBe("captured");
  expect(answered.failure_reason).toBeNull();
  expect(answered.answer).toBeTruthy();
  // A captured answer opens the refinement window with the full budget.
  expect(answered.refinements_remaining).toBe(3);
  expect(answered.window_expires_at).toBeTruthy();
});
