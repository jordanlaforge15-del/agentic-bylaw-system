// Smoke: /billing renders the dormant-state copy and does not crash
// when the FastAPI billing router returns 503 (ADVISOR_BILLING_ENABLED
// is false by default in the e2e_server wiring).
//
// Catches the class of bug where a frontend page assumes the upstream
// returns 200 and dereferences null on 503.

import { expect, test } from "../fixtures/test-env";

test("/billing handles the dormant 503 state without crashing", async ({
  page,
}) => {
  await page.goto("/billing");
  await expect(
    page.getByRole("heading", { level: 1, name: /Billing/ }),
  ).toBeVisible();
  await expect(
    page.getByText(/Billing is dormant on this deployment/),
  ).toBeVisible();
});
