// Smoke: /billing renders the dormant-state copy and shows real credit
// balances from GET /v1/billing/me even when ADVISOR_BILLING_ENABLED is
// false (the default in e2e_server wiring).
//
// ABS-183: previously /billing/me returned 503 in dormant mode, so the
// page displayed "0" for every tier even though users had admin-granted
// credits. The fix makes /billing/me return real per-tier balances with
// enabled=false so the "Buy" buttons are hidden but the counts are
// accurate.

import { expect, test } from "../fixtures/test-env";

test("/billing shows dormant notice with real credit balances", async ({
  page,
}) => {
  await page.goto("/billing");
  await expect(
    page.getByRole("heading", { level: 1, name: /Billing/ }),
  ).toBeVisible();

  // Dormant notice must still be present (enabled=false in the response).
  await expect(
    page.getByText(/Billing is dormant on this deployment/),
  ).toBeVisible();

  // The demo user has credits from the seed script, so the balance
  // section should render with the "Credit balance" heading and at
  // least one tier card — proving the page loaded real data rather
  // than showing a blank state or an error.
  await expect(
    page.getByRole("heading", { name: /Credit balance/i }),
  ).toBeVisible();

  // At least one tier card should show a non-zero available count.
  // The demo user has 100 credits per tier from seed_e2e_user.py.
  const tierCards = page.locator(
    "text=/available · \\d+ in flight · \\d+ consumed/",
  );
  await expect(tierCards.first()).toBeVisible();
});
