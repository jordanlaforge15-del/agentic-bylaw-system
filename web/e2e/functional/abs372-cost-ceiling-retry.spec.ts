// Functional: ABS-372 — a nondeterministic cost_ceiling trip on the
// Variance justification package ($299) is RETRIED before voiding, so a
// routine input still completes with a captured answer on the user's
// single request.
//
// The bug (after ABS-370/371):
//   ABS-370 raised the grounding-heavy SKUs' cumulative reasoning budgets
//   to 260k, but the Variance ($299) run still voided on ~1 of 2 routine
//   runs — the cost_ceiling trip is a nondeterministic draw of the
//   token-usage dice, and any fixed ceiling still has a tail past it.
//   Chasing budget headroom neither closes that tail nor comes free (it
//   inflates every routine run's cost cap).
//
// The fix:
//   src/advisor/billing/answers.py — run_answer now RETRIES a cost_ceiling
//   trip on a fresh turn (a new ChatSession = an independent draw) up to
//   COST_CEILING_MAX_ATTEMPTS times before voiding. The customer is charged
//   exactly once, on the attempt that grounds. Only cost_ceiling is retried
//   (zero_evidence / internal_error are deterministic).
//
// What this spec guards:
//   The whole retry-then-capture path through the REAL buy-an-answer service
//   (checkout → webhook authorize → run → settle) over the e2e FastAPI +
//   Postgres + MockGateway stack. The MOCK_COST_CEILING_ONCE sentinel makes
//   the FIRST answer attempt genuinely trip the ABS-305 cumulative breaker
//   (a real cost_ceiling void), then grounds cheaply on the retry — so this
//   run would settle "voided" on the pre-fix code and settles "captured" now.
//
//   The per-attempt budget ARITHMETIC (that 260k grounds where 165k voids)
//   stays pinned in the Python unit tests
//   (tests/advisor/billing/test_buy_answer.py) — a token-heuristic property
//   the MockGateway can't reproduce faithfully, same split as abs370. Here we
//   guard the resilience mechanism end-to-end; there we guard the numbers.

import { expect, test } from "@playwright/test";

import { E2E_API_URL } from "../fixtures/test-env";

function uniqueTag(): string {
  return `abs372-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`;
}

// The ticket's own repro: 5686 Spring Garden Rd, side-yard setback variance
// for a rear addition. The MOCK_COST_CEILING_ONCE[<nonce>] sentinel (carried
// in the hardship rationale, which the prompt template renders verbatim)
// makes the first attempt trip the cumulative breaker and every retry ground.
function varianceInputs(nonce: string): Record<string, string> {
  return {
    address: "5686 Spring Garden Rd",
    requested_variance:
      "reduce the required side-yard setback from 2.5 m to 1.8 m for a rear addition",
    hardship_rationale:
      "the addition aligns with the existing wall on a 12.2 m frontage " +
      `MOCK_COST_CEILING_ONCE[${nonce}]`,
  };
}

async function checkout(
  request: import("@playwright/test").APIRequestContext,
  userId: string,
  inputs: Record<string, string>,
) {
  const res = await request.post(
    `${E2E_API_URL}/v1/_test/buy-answer/checkout`,
    {
      data: {
        user_id: userId,
        question_slug: "variance_justification",
        inputs,
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

test("a cost_ceiling trip on the $299 variance package is retried and captures", async ({
  request,
}) => {
  const tag = uniqueTag();
  const userId = tag;

  const created = await checkout(request, userId, varianceInputs(tag));
  expect(created.status).toBe("authorized");
  expect(created.question_slug).toBe("variance_justification");

  const answered = await runAnswer(request, created.purchase_id);

  // The first attempt tripped the ABS-305 cumulative breaker (cost_ceiling);
  // the retry grounded. The paid deliverable must CAPTURE — not settle on the
  // "ran past its reasoning budget" hard failure the pre-fix code produced.
  expect(
    answered.status,
    `expected captured but got ${answered.status} ` +
      `(failure_reason=${answered.failure_reason}) — the ABS-372 ` +
      "retry-before-void path may have regressed",
  ).toBe("captured");
  expect(answered.failure_reason).toBeNull();
  expect(answered.answer).toBeTruthy();
  // A captured answer opens the refinement window with the full budget —
  // the retry recovered a genuine paid answer, not a degraded stub.
  expect(answered.refinements_remaining).toBe(3);
  expect(answered.window_expires_at).toBeTruthy();
});
