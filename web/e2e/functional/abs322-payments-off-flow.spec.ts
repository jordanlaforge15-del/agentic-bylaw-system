// Functional: ABS-390 — the payments-OFF (trial-only) billing posture.
//
// Reconciled from the old ABS-322 free-question buy-answer flow to the beta
// pivot posture (docs/decisions/2026-07-beta-pivot-turn-wallet-gated-reports.md,
// D4/D8): billing is ON, payments are OFF, and the token wallet is funded by
// the one-time signup grant. In that posture:
//
//   * the wallet reports payments_enabled=false (no paid top-ups);
//   * POST /v1/billing/checkout/topup answers 503 payments_disabled — the
//     "paid top-ups coming soon" state, never a dead checkout;
//   * the retired case-credit pack checkout answers 410 packs_retired — a
//     permanent, explicit retirement, not a transient 503.
//
// The e2e stack runs billing DORMANT (payments_enabled=false), which is the
// payments-off posture at the HTTP boundary, so these assertions run against
// the real advisor process. Calls FastAPI directly via X-Test-User-Id (the
// Next proxy binds the upstream user id to ADVISOR_DEMO_USER_ID at process
// start, so per-user API tests bypass it).

import { expect, test } from "@playwright/test";

import { E2E_API_URL } from "../fixtures/test-env";

function uniqueUser(tag: string): string {
  return `abs322-${tag}-${Date.now()}-${Math.random()
    .toString(36)
    .slice(2, 8)}`;
}

test("payments off: the wallet advertises payments_enabled=false", async ({
  request,
}) => {
  const userId = uniqueUser("wallet");
  const res = await request.get(`${E2E_API_URL}/v1/billing/wallet`, {
    headers: { "X-Test-User-Id": userId },
  });
  expect(res.status(), await res.text()).toBe(200);
  const body = (await res.json()) as {
    payments_enabled: boolean;
    tokens_per_turn: number;
  };
  // Trial-only posture: no paid top-ups.
  expect(body.payments_enabled).toBe(false);
  // Turns conversion is backend-owned and always present.
  expect(body.tokens_per_turn).toBeGreaterThan(0);
});

test("payments off: paid top-ups are refused with 503 payments_disabled", async ({
  request,
}) => {
  const userId = uniqueUser("topup");
  const res = await request.post(
    `${E2E_API_URL}/v1/billing/checkout/topup`,
    {
      headers: { "X-Test-User-Id": userId },
      data: { sku: "small" },
    },
  );
  // The "paid top-ups coming soon" state — a clean refusal, not a dead
  // checkout that returns no URL.
  expect(res.status(), await res.text()).toBe(503);
  const body = (await res.json()) as { detail: { code: string } };
  expect(body.detail.code).toBe("payments_disabled");
});

test("retired: the legacy pack checkout answers 410 packs_retired", async ({
  request,
}) => {
  const userId = uniqueUser("pack");
  const res = await request.post(
    `${E2E_API_URL}/v1/billing/checkout/pack`,
    {
      headers: { "X-Test-User-Id": userId },
      data: { tier: "standard", pack_sku: "starter" },
    },
  );
  // The beta pivot retired the case-credit tier×pack catalog product-wide;
  // the endpoint answers a permanent 410 Gone, not a transient 503.
  expect(res.status(), await res.text()).toBe(410);
  const body = (await res.json()) as { detail: { code: string } };
  expect(body.detail.code).toBe("packs_retired");
});
