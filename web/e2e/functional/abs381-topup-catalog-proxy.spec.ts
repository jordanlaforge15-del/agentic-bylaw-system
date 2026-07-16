// Functional: ABS-381 — token top-up catalog, proxy contract.
//
// Drives the Next.js proxy end to end (browser → /api/billing/topups →
// FastAPI) and asserts the PUBLIC top-up catalog shape:
//   * payments_enabled + tokens_per_turn are present.
//   * options are the small/medium/large SKUs with server-side token
//     quantities and CAD prices.
//   * turns conversion is backend-owned: approx_turns == floor(tokens /
//     tokens_per_turn) — the client never divides tokens itself.
//   * available == payments_enabled AND price configured.
//
// Payment postures: the e2e stack runs billing DORMANT (payments off), so
// the catalog is served with payments_enabled == false and every option
// unavailable ("coming soon") — the posture asserted here. The payments-ON
// posture (available flips true when a Price ID is configured) is covered by
// the unit suite (tests/advisor/billing/test_topup_router.py), which can flip
// ADVISOR_PAYMENTS_ENABLED per-case.
//
// The endpoint is skipAuth (the pricing page renders signed-out), so the
// same public shape must come back whether or not a session cookie is
// present — this spec checks both.

import {
  expect,
  mintTestIdentity,
  signInAs,
  test,
} from "../auth/fixtures";

type TopupOption = {
  sku: string;
  display_name: string;
  tokens: number;
  approx_turns: number;
  price_cents: number;
  available: boolean;
};

type TopupCatalog = {
  payments_enabled: boolean;
  currency: string;
  tokens_per_turn: number;
  options: TopupOption[];
};

function assertPublicShape(body: TopupCatalog): void {
  // Dormant stack → payments off → nothing purchasable.
  expect(body.payments_enabled).toBe(false);
  expect(body.currency).toBe("CAD");
  expect(typeof body.tokens_per_turn).toBe("number");
  expect(body.tokens_per_turn).toBeGreaterThan(0);

  expect(body.options.map((o) => o.sku)).toEqual([
    "small",
    "medium",
    "large",
  ]);

  for (const opt of body.options) {
    expect(opt.tokens).toBeGreaterThan(0);
    expect(opt.price_cents).toBeGreaterThan(0);
    expect(typeof opt.display_name).toBe("string");
    // Backend-owned turns conversion.
    expect(opt.approx_turns).toBe(
      Math.floor(opt.tokens / body.tokens_per_turn),
    );
    // Payments off → every option unavailable regardless of price config.
    expect(opt.available).toBe(false);
  }

  // Server-side truth for token quantities (design-spec catalog).
  const bySku = Object.fromEntries(body.options.map((o) => [o.sku, o]));
  expect(bySku.small.tokens).toBe(20_000);
  expect(bySku.medium.tokens).toBe(75_000);
  expect(bySku.large.tokens).toBe(200_000);
}

test("top-up catalog is public via /api/billing/topups (signed-out)", async ({
  context,
}) => {
  // No signInAs — the endpoint is skipAuth, so this must still return 200.
  const res = await context.request.get("/api/billing/topups");
  expect(res.status(), `topups expected 200: ${await res.text()}`).toBe(200);
  assertPublicShape((await res.json()) as TopupCatalog);
});

test("top-up catalog shape is stable for a signed-in user", async ({
  context,
}) => {
  await signInAs(context, mintTestIdentity("abs381"));
  const res = await context.request.get("/api/billing/topups");
  expect(res.status(), `topups expected 200: ${await res.text()}`).toBe(200);
  assertPublicShape((await res.json()) as TopupCatalog);
});
