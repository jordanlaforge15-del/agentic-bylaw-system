// Functional: /pricing "Pay by the turn" — trial + top-up amounts (ABS-387).
//
// The pricing page sells the conversation: a free-trial TrialCard plus three
// paid TopUpCards, all data-driven from GET /api/billing/topups. The turn
// counts and CAD prices are SERVER-side truth (never hardcoded in the page),
// so this spec reads the live catalog off the wire and asserts the rendered
// card mirrors it exactly — a backend catalog edit surfaces here.
//
// The e2e stack runs billing DORMANT (payments off), so every top-up is
// unavailable ("coming soon") and the private-beta banner is shown — the
// posture asserted here. The prices ($15 / $50 / $120 CAD) are locked to the
// design-spec catalog (src/advisor/billing/topups.py).

import { expect, test } from "../fixtures/test-env";

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

test("pricing page renders trial + top-up cards from the live catalog", async ({
  page,
}) => {
  // Read the server-side truth first so the DOM assertions compare against
  // the wire, not hardcoded numbers.
  const res = await page.request.get("/api/billing/topups");
  expect(res.ok(), `topups expected 200: ${await res.text()}`).toBeTruthy();
  const catalog = (await res.json()) as TopupCatalog;

  // Dormant stack → payments off → nothing purchasable.
  expect(catalog.payments_enabled).toBe(false);
  const bySku = Object.fromEntries(catalog.options.map((o) => [o.sku, o]));
  expect(bySku.small.price_cents).toBe(1500);
  expect(bySku.medium.price_cents).toBe(5000);
  expect(bySku.large.price_cents).toBe(12000);

  await page.goto("/pricing");

  // Header identifies the turns surface.
  await expect(page.locator("body")).toContainText("PRICING · TURNS");
  await expect(
    page.getByRole("heading", { level: 1, name: /Pay by the turn/i }),
  ).toBeVisible();

  // TrialCard — free signup grant.
  const trial = page.getByTestId("trial-card");
  await expect(trial).toContainText("ON SIGNUP");
  await expect(trial).toContainText("Free");
  await expect(trial).toContainText("Request an invite");

  // Three TopUpCards, each mirroring the live catalog: price + ~turns.
  for (const sku of ["small", "medium", "large"] as const) {
    const opt = bySku[sku];
    const card = page.getByTestId(`topup-card-${sku}`);
    await expect(card).toBeVisible();
    // Price in CAD dollars (whole-dollar catalog values).
    await expect(card).toContainText(`$${opt.price_cents / 100}`);
    // Backend-owned turns conversion rendered verbatim.
    await expect(card).toContainText(`~${opt.approx_turns} turns`);
    // Payments off → coming soon, no live checkout.
    await expect(card).toContainText(/COMING SOON/i);
  }

  // Middle SKU is the highlighted best value.
  await expect(page.getByTestId("topup-card-medium")).toContainText(
    /BEST VALUE/i,
  );

  // Payments off → private-beta banner.
  await expect(page.getByTestId("beta-banner")).toContainText(
    "You're in the private beta",
  );
});

test("pricing page shows the degraded card with Retry when the catalog fails", async ({
  page,
}) => {
  // Fail the top-up catalog fetch; the page must degrade gracefully to the
  // designed PRICING UNAVAILABLE card (no 500, no blank page) with a Retry
  // affordance rather than throwing.
  let fail = true;
  await page.route("**/api/billing/topups", async (route) => {
    if (fail) {
      await route.fulfill({
        status: 503,
        contentType: "application/json",
        body: JSON.stringify({ detail: "unavailable" }),
      });
    } else {
      await route.fetch().then((r) => route.fulfill({ response: r }));
    }
  });

  await page.goto("/pricing");
  const degraded = page.getByTestId("pricing-unavailable");
  await expect(degraded).toContainText(/PRICING UNAVAILABLE/i);
  const retry = page.getByTestId("pricing-retry");
  await expect(retry).toBeVisible();

  // Retry succeeds once the catalog is reachable again.
  fail = false;
  await retry.click();
  await expect(page.getByTestId("trial-card")).toBeVisible();
  await expect(page.getByTestId("pricing-unavailable")).toHaveCount(0);
});
