// Integration (ABS-391): both payment postures exercised via env.
//
// The beta ships two billing postures, selected by env (D8 of the pivot ADR):
//
//   * trial-only  — ADVISOR_PAYMENTS_ENABLED=false. Signup grants a starting
//     token balance; paid top-ups are refused (503 payments_disabled); an
//     answer is unlocked by a free-question credit, never a card charge.
//   * payments-on — ADVISOR_PAYMENTS_ENABLED=true (Stripe wired). Top-ups and
//     priced-answer purchases capture a real card hold.
//
// The e2e app server boots in the TRIAL posture (e2e-up.sh sets no payments
// flag → ADVISOR_PAYMENTS_ENABLED defaults false), so the trial assertions run
// against the live app/proxy. The PAYMENTS-ON posture is exercised through the
// /v1/_test/buy-answer/* harness, which constructs a billing subsystem with
// ADVISOR_PAYMENTS_ENABLED=true + Stripe price ids and a MockStripeClient — the
// same env flip a paid deployment makes, without a real Stripe account.
//
// This spec is the single integration checkpoint that BOTH postures are
// reachable in the merged build. The individual posture behaviours have deeper
// coverage in abs312 (paid buy-answer), abs322 (trial buy-answer), abs379
// (public catalog posture) and topup-checkout (frontend top-up return).

import { expect, test } from "@playwright/test";

import { E2E_API_URL } from "../fixtures/test-env";

type Req = import("@playwright/test").APIRequestContext;

function uniqueUser(tag: string): string {
  return `abs391-${tag}-${Date.now()}-${Math.random()
    .toString(36)
    .slice(2, 8)}`;
}

const VALID_INPUTS = {
  address: "1234 Elm St",
  proposed_use: "a four-unit dwelling",
};

test.describe("ABS-391 payment-posture matrix", () => {
  test("trial-only posture: public catalog is payments-off, paid top-up is refused, free answer captures", async ({
    request,
  }) => {
    // The public top-up catalog advertises the posture. On the trial
    // deployment payments are off, so every SKU is present-but-unavailable.
    const topups = await request.get(`${E2E_API_URL}/v1/billing/topups`);
    expect(topups.status(), await topups.text()).toBe(200);
    const catalog = (await topups.json()) as {
      payments_enabled: boolean;
      options: Array<{ sku: string; available: boolean }>;
    };
    expect(catalog.payments_enabled).toBe(false);
    expect(catalog.options.length).toBeGreaterThan(0);
    expect(catalog.options.every((o) => o.available === false)).toBe(true);

    // A paid top-up checkout is refused with 503 payments_disabled — there is
    // no Stripe on a trial deployment.
    const checkout = await request.post(
      `${E2E_API_URL}/v1/billing/checkout/topup`,
      { data: { sku: "small" } },
    );
    expect(checkout.status(), await checkout.text()).toBe(503);
    const refusal = (await checkout.json()) as {
      detail: { code: string };
    };
    expect(refusal.detail.code).toBe("payments_disabled");

    // Yet trial billing is fully functional: a free-question credit unlocks a
    // grounded answer with NO card charge.
    const userId = uniqueUser("trial");
    const granted = await request.post(
      `${E2E_API_URL}/v1/_test/buy-answer/grant-free-questions`,
      { data: { user_id: userId, quantity: 1 } },
    );
    expect(granted.status(), await granted.text()).toBe(200);

    const created = await request.post(
      `${E2E_API_URL}/v1/_test/buy-answer/checkout-free`,
      { data: { user_id: userId, question_slug: "permitted_use", inputs: VALID_INPUTS } },
    );
    expect(created.status(), await created.text()).toBe(200);
    const createdBody = (await created.json()) as {
      ok: boolean;
      status: string;
      purchase_id: number;
      free_questions_remaining: number;
    };
    expect(createdBody.ok).toBe(true);
    expect(createdBody.status).toBe("authorized");
    expect(createdBody.free_questions_remaining).toBe(0);

    const answered = await request.post(
      `${E2E_API_URL}/v1/_test/buy-answer/answer`,
      { data: { purchase_id: createdBody.purchase_id } },
    );
    expect(answered.status(), await answered.text()).toBe(200);
    const answeredBody = (await answered.json()) as { status: string };
    // Captured against the free credit — no Stripe capture, credit stays spent.
    expect(answeredBody.status).toBe("captured");
  });

  test("payments-on posture: a priced answer authorizes then captures the card", async ({
    request,
  }) => {
    // The buy-answer harness runs the REAL billing subsystem with
    // ADVISOR_PAYMENTS_ENABLED=true + Stripe price ids (MockStripeClient) — the
    // paid posture a Stripe-wired deployment ships.
    const userId = uniqueUser("paid");
    const created = await request.post(
      `${E2E_API_URL}/v1/_test/buy-answer/checkout`,
      { data: { user_id: userId, question_slug: "permitted_use", inputs: VALID_INPUTS } },
    );
    expect(created.status(), await created.text()).toBe(200);
    const createdBody = (await created.json()) as {
      status: string;
      purchase_id: number;
    };
    // Checkout + webhook authorization placed a card hold (not a free credit).
    expect(createdBody.status).toBe("authorized");

    const answered = await request.post(
      `${E2E_API_URL}/v1/_test/buy-answer/answer`,
      { data: { purchase_id: createdBody.purchase_id } },
    );
    expect(answered.status(), await answered.text()).toBe(200);
    const answeredBody = (await answered.json()) as {
      status: string;
      failure_reason: string | null;
    };
    // A grounded answer captures the authorized card — paid billing works.
    expect(answeredBody.status).toBe("captured");
    expect(answeredBody.failure_reason).toBeNull();
  });
});
