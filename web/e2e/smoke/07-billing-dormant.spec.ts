// Smoke: /billing renders the unified turns-based account view in the
// payments-off (dormant) posture — ABS-388.
//
// The billing page shares one BillingContent component across /billing
// (marketing chrome) and /app/billing (AuthShell). It client-fetches the
// turns-aware wallet, the wallet ledger, owned reports, and cases from the
// /api proxies. In the e2e stack ADVISOR_BILLING_ENABLED is false, so the
// wallet comes back payments_enabled=false and the page shows:
//   * a "~N turns" balance headline (approx_turns_remaining, never a raw
//     token count),
//   * the free-trial beta banner ("paid top-ups open soon"),
//   * the wallet ledger with at least the seed grant row.
//
// The demo user is seeded with a large token wallet (scripts/seed_e2e_user
// tops it up to a huge floor), so the turns headline and a grant/top-up
// ledger row are always present. This proves the client fetch hydrated real
// data rather than a blank/error state — and that NO tier/credit/pack
// vocabulary leaks into the copy.

import { expect, test } from "../fixtures/test-env";

test("/billing shows the turns balance, beta banner and wallet ledger (dormant)", async ({
  page,
}) => {
  await page.goto("/billing");

  await expect(
    page.getByRole("heading", { level: 1, name: /Billing/ }),
  ).toBeVisible();

  // Turns headline — "~N turns", sourced from approx_turns_remaining.
  const turns = page.getByTestId("billing-turns");
  await expect(turns).toBeVisible({ timeout: 15_000 });
  await expect(turns).toContainText(/~\d[\d,]*\s+turns?/i);

  // Payments-off beta banner replaces any purchase CTA.
  await expect(page.getByTestId("billing-beta-banner")).toBeVisible();
  await expect(page.getByTestId("billing-beta-banner")).toContainText(
    /paid top-ups open soon/i,
  );
  // No inline top-up buttons when payments are off.
  await expect(page.getByTestId("billing-topup-btn")).toHaveCount(0);

  // Wallet ledger: the seed leaves at least one grant/top-up row, each
  // shown as a turns delta (never a raw token count).
  await expect(page.getByTestId("billing-transactions")).toBeVisible();
  await expect(page.getByTestId("billing-tx-row").first()).toBeVisible();
  await expect(page.getByTestId("billing-tx-row").first()).toContainText(
    /turns?/i,
  );

  // Reports + cases cards render (empty-state or rows — both are fine).
  await expect(page.getByTestId("billing-reports")).toBeVisible();
  await expect(page.getByTestId("billing-cases")).toBeVisible();

  // No tier / credit / pack vocabulary anywhere in the copy.
  const body = page.locator("body");
  await expect(body).not.toContainText(/\bcredits?\b/i);
  await expect(body).not.toContainText(/\bpack\b/i);
  await expect(body).not.toContainText(
    /Quick Lookup|Standard Case|Complex File/i,
  );
  await expect(body).not.toContainText(/\btier\b/i);
});

test("/app/billing renders the same turns view inside the authorized shell", async ({
  page,
}) => {
  await page.goto("/app/billing");

  await expect(
    page.getByRole("heading", { level: 1, name: /Billing/ }),
  ).toBeVisible();

  const turns = page.getByTestId("billing-turns");
  await expect(turns).toBeVisible({ timeout: 15_000 });
  await expect(turns).toContainText(/~\d[\d,]*\s+turns?/i);

  await expect(page.getByTestId("billing-beta-banner")).toBeVisible();
  await expect(page.getByTestId("billing-transactions")).toBeVisible();
});
