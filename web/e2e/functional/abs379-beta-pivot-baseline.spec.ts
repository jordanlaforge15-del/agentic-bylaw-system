// Functional: ABS-379 beta-pivot baseline characterization.
//
// ABS-379 is the umbrella "design spec & tracking" issue for the beta
// pivot (ADR docs/decisions/2026-07-beta-pivot-turn-wallet-gated-reports.md).
// The 12 sub-issues (ABS-380..391) carry the runtime changes: a turn-based
// token wallet, top-up SKUs, and per-report gates that REPLACE today's
// credit/tier chat-billing model.
//
// This spec locks in the beta-pivot posture so that when the sub-issues
// land, each flip is visible as an intentional edit to these assertions
// rather than a silent regression. Two anchors:
//
//   1. The D6 API contract. I1 (wallet, ABS-380) has LANDED:
//      GET /v1/billing/wallet is now registered and auth-gated (401 with
//      no credentials, no longer 404). I2 (top-up SKUs) has NOT shipped
//      yet, so GET /v1/billing/topups is still route-not-found (404).
//      When I2 lands, the topups assertion flips to 401/200 too.
//
//   2. The /billing surface still speaks the retired credit/tier vocabulary
//      the ADR pivots away from ("Credit balance"), and shows NO turns /
//      wallet vocabulary yet — the "inverse posture" the ADR Context calls
//      out. When I9 (billing pages) ships, this flips to turns.
//
// Driven against the real local stack (Next proxy -> FastAPI). No stubs.

import { expect, test } from "../fixtures/test-env";
import { E2E_API_URL } from "../fixtures/test-env";

test.describe("ABS-379 beta-pivot baseline", () => {
  test("D6: wallet endpoint is live (auth-gated), top-up endpoint not yet", async ({
    request,
  }) => {
    // I1 (ABS-380) landed the wallet: the route is registered and requires
    // auth, so an unauthenticated request is rejected (401) rather than
    // route-not-found (404). This is the intentional flip from the pre-pivot
    // 404 that proves the endpoint arrived.
    const wallet = await request.get(`${E2E_API_URL}/v1/billing/wallet`);
    expect(wallet.status(), await wallet.text()).toBe(401);

    // I2 (top-up SKUs) has not shipped yet: this path is still simply not
    // registered on the router. When I2 lands, this flips to 401/200.
    const topups = await request.get(`${E2E_API_URL}/v1/billing/topups`);
    expect(topups.status(), await topups.text()).toBe(404);
  });

  test("/billing still shows the retired credit/tier model, not turns", async ({
    page,
  }) => {
    await page.goto("/billing");
    await expect(
      page.getByRole("heading", { level: 1, name: /Billing/ }),
    ).toBeVisible();

    // Pre-pivot: the credit balance surface is the billing vocabulary.
    await expect(
      page.getByRole("heading", { name: /Credit balance/i }),
    ).toBeVisible();

    // The pivot introduces turns/wallet vocabulary; it must NOT be present
    // yet on this surface. (Case-insensitive, whole-page assertion.)
    await expect(
      page.getByText(/\bturns?\s+(remaining|balance)\b/i),
    ).toHaveCount(0);
    await expect(page.getByText(/top[\s-]?up/i)).toHaveCount(0);
  });
});
