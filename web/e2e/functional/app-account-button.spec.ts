// Functional: navigation between /app and /app/billing now goes through
// the shared workspace menu (AccountMenu) — the single authorized nav
// control introduced in ABS-334. The old per-page "Billing" button and
// the bespoke "Back to chat" link were removed in favour of this menu, so
// these specs exercise the menu path that replaced them.

import { expect, test, openCaseViaApi } from "../fixtures/test-env";

test("app header: workspace menu navigates to /app/billing", async ({
  page,
}) => {
  // Open a case to have a valid case context.
  const { caseId } = await openCaseViaApi();

  await page.goto(`/app?case_id=${caseId}`);

  // The compact workspace menu lives in the app header.
  await page.getByRole("button", { name: "Workspace menu" }).click();
  const menu = page.getByRole("menu");
  await expect(menu).toBeVisible();

  // The Billing row routes to /app/billing.
  await menu.getByRole("menuitem", { name: /Billing/ }).click();
  await page.waitForURL(/\/app\/billing/, { timeout: 10000 });
  await expect(page.getByRole("heading", { name: /Billing/ })).toBeVisible();
});

test("app billing page: workspace menu returns to Readings (/app)", async ({
  page,
}) => {
  await page.goto("/app/billing");
  await expect(page.getByRole("heading", { name: /Billing/ })).toBeVisible();

  // The same workspace menu (now in the AuthBar) routes back to the chat
  // workspace via the Readings row.
  await page.getByRole("button", { name: "Workspace menu" }).click();
  const menu = page.getByRole("menu");
  await expect(menu).toBeVisible();
  await menu.getByRole("menuitem", { name: /Readings/ }).click();

  // Should land on the chat workspace root.
  await page.waitForURL(/\/app(\?.*)?$/, { timeout: 10000 });
});
