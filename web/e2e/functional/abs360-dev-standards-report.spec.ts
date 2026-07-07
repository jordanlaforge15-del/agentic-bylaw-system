// Functional: ABS-360 — the flagship Development-standards compliance
// report ($149) completes for a routine multi-standard input.
//
// The bug:
//   The dev-standards question evaluates every submitted dimension
//   (height, four setbacks, lot coverage, FAR, parking) against the
//   zone's standards — get_zone_profile returns the full standards table
//   and evaluate_submission_against_bylaws grounds each standard in turn.
//   That grounding-heavy run reliably out-grew the flat 165k default
//   cumulative budget and tripped the ABS-305 breaker BEFORE it could
//   ground, settling the purchase voided/"cost_ceiling" — the user saw
//   "This question ran past its reasoning budget… You were not charged."
//   on the highest-intent purchase path.
//
// The fix:
//   The catalog question now carries its own, larger cumulative ceiling
//   (260k, matching the off-menu "complex" tier) so a routine input
//   grounds and captures. See src/advisor/billing/questions.py and the
//   threading in advisor.billing.answers._resolve_run_inputs.
//
// What this spec guards:
//   The flagship purchase runs end-to-end through the REAL buy-an-answer
//   service (checkout → webhook authorize → run → settle) over the e2e
//   FastAPI + Postgres + MockGateway stack and CAPTURES a grounded
//   answer — the report path that was hard-failing is exercised whole.
//
//   The budget ARITHMETIC (that 260k grounds a load which the 165k
//   default would void) is a token-heuristic property the MockGateway
//   can't reproduce faithfully end-to-end, so it is pinned in the Python
//   unit test tests/advisor/billing/test_buy_answer.py
//   ::test_dev_standards_grounds_where_the_default_budget_would_void —
//   the same split as abs291/abs305 (heavy-budget math in Python, path
//   guarded end-to-end here). Reverting the budget bump turns that Python
//   test red.

import { expect, test } from "@playwright/test";

import { E2E_API_URL } from "../fixtures/test-env";

function uniqueUser(tag: string): string {
  return `abs360-${tag}-${Date.now()}-${Math.random()
    .toString(36)
    .slice(2, 8)}`;
}

// The ticket's routine input: a normal 4-storey multi-unit proposal.
const DEV_STANDARDS_INPUTS = {
  address: "1250 Robie St, Halifax",
  project_details:
    "Proposed 4-storey multi-unit residential building: 14.5 m height, " +
    "12 units, front setback 3.0 m, rear setback 5.0 m, side setbacks " +
    "1.5 m each, lot coverage 48%, 8 parking spaces.",
};

async function checkout(
  request: import("@playwright/test").APIRequestContext,
  userId: string,
) {
  const res = await request.post(
    `${E2E_API_URL}/v1/_test/buy-answer/checkout`,
    {
      data: {
        user_id: userId,
        question_slug: "development_standards",
        inputs: DEV_STANDARDS_INPUTS,
      },
    },
  );
  expect(res.status(), await res.text()).toBe(200);
  return res.json();
}

async function runAnswer(
  request: import("@playwright/test").APIRequestContext,
  purchaseId: number,
) {
  const res = await request.post(`${E2E_API_URL}/v1/_test/buy-answer/answer`, {
    data: { purchase_id: purchaseId },
  });
  expect(res.status(), await res.text()).toBe(200);
  return res.json();
}

test("the $149 development-standards report completes and captures for a routine input", async ({
  request,
}) => {
  const userId = uniqueUser("report");

  const created = await checkout(request, userId);
  expect(created.status).toBe("authorized");
  expect(created.question_slug).toBe("development_standards");

  const answered = await runAnswer(request, created.purchase_id);

  // The flagship deliverable produced a grounded report — NOT the
  // "ran past its reasoning budget" (cost_ceiling) hard failure.
  expect(
    answered.status,
    `expected captured but got ${answered.status} ` +
      `(failure_reason=${answered.failure_reason}) — the dev-standards ` +
      "budget fix (ABS-360) may have regressed",
  ).toBe("captured");
  expect(answered.failure_reason).toBeNull();
  expect(answered.answer).toBeTruthy();
  // A captured answer opens the refinement window with the full budget.
  expect(answered.refinements_remaining).toBe(3);
  expect(answered.window_expires_at).toBeTruthy();
});
