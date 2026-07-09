// Functional: ABS-370 — the Variance justification package ($299) and the
// Zoning due-diligence summary ($199) complete for routine inputs.
//
// The bug (sibling of ABS-360):
//   ABS-360 raised only the development-standards question's cumulative
//   reasoning budget; Variance and Zoning still rode the flat 165k
//   default. Replaying the ABS-305 estimator over real run transcripts
//   showed both SKUs' routine runs straddle that ceiling (variance:
//   captured at 131k / voided at 171.6k on the same code; due-diligence:
//   captured at 158.8k / voided at 193.0k) — nondeterministic
//   cost_ceiling voids at the Verifying-citations step.
//
// The fix:
//   Both catalog questions now carry their own 260k cumulative ceiling
//   (matching the off-menu "complex" tier and the ABS-360 precedent).
//   See src/advisor/billing/questions.py and the threading in
//   advisor.billing.answers._resolve_run_inputs.
//
// What this spec guards:
//   Both purchases run end-to-end through the REAL buy-an-answer service
//   (checkout → webhook authorize → run → settle) over the e2e FastAPI +
//   Postgres + MockGateway stack and CAPTURE a grounded answer — the
//   report paths that were nondeterministically hard-failing are
//   exercised whole.
//
//   The budget ARITHMETIC (that 260k grounds a load which the 165k
//   default would void) is a token-heuristic property the MockGateway
//   can't reproduce faithfully end-to-end, so it is pinned in the Python
//   unit test tests/advisor/billing/test_buy_answer.py
//   ::test_variance_and_due_diligence_ground_where_the_default_would_void
//   — the same split as abs360 (heavy-budget math in Python, path
//   guarded end-to-end here). Reverting either budget bump turns that
//   Python test red.

import { expect, test } from "@playwright/test";

import { E2E_API_URL } from "../fixtures/test-env";

function uniqueUser(tag: string): string {
  return `abs370-${tag}-${Date.now()}-${Math.random()
    .toString(36)
    .slice(2, 8)}`;
}

// Routine inputs mirroring the runs that voided on 2026-07-07.
const SKUS = [
  {
    tag: "variance",
    slug: "variance_justification",
    inputs: {
      address: "6242 North St, Halifax",
      requested_variance:
        "reduce the required rear-yard setback from 7.5 m to 5.0 m",
      hardship_rationale:
        "irregular pie-shaped lot with a steep rear grade",
    },
    label: "$299 variance justification package",
  },
  {
    tag: "zoning",
    slug: "due_diligence",
    inputs: {
      address: "2367 Brunswick St, Halifax",
    },
    label: "$199 zoning due-diligence summary",
  },
] as const;

async function checkout(
  request: import("@playwright/test").APIRequestContext,
  userId: string,
  slug: string,
  inputs: Record<string, string>,
) {
  const res = await request.post(
    `${E2E_API_URL}/v1/_test/buy-answer/checkout`,
    {
      data: {
        user_id: userId,
        question_slug: slug,
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

for (const sku of SKUS) {
  test(`the ${sku.label} completes and captures for a routine input`, async ({
    request,
  }) => {
    const userId = uniqueUser(sku.tag);

    const created = await checkout(request, userId, sku.slug, {
      ...sku.inputs,
    });
    expect(created.status).toBe("authorized");
    expect(created.question_slug).toBe(sku.slug);

    const answered = await runAnswer(request, created.purchase_id);

    // The paid deliverable produced a grounded report — NOT the
    // "ran past its reasoning budget" (cost_ceiling) hard failure.
    expect(
      answered.status,
      `expected captured but got ${answered.status} ` +
        `(failure_reason=${answered.failure_reason}) — the ${sku.slug} ` +
        "budget fix (ABS-370) may have regressed",
    ).toBe("captured");
    expect(answered.failure_reason).toBeNull();
    expect(answered.answer).toBeTruthy();
    // A captured answer opens the refinement window with the full budget.
    expect(answered.refinements_remaining).toBe(3);
    expect(answered.window_expires_at).toBeTruthy();
  });
}
