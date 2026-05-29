import { expect, test, openCaseViaApi } from "../fixtures/test-env";

test("app header: Billing button navigates to /app/billing", async ({
  page,
}) => {
  // Open a case to have a valid case context
  const { caseId } = await openCaseViaApi();

  // Navigate to /app with the case
  await page.goto(`/app?case_id=${caseId}`);
  await page.waitForLoadState("networkidle");

  // Look for the Billing button by text
  const billingButton = page.locator('a[href="/app/billing"]');

  // Verify the link exists, is visible, and labeled "Billing"
  await expect(billingButton).toBeVisible();
  await expect(billingButton).toContainText("Billing");

  // Click the Billing button
  await billingButton.click();

  // Verify we navigated to /app/billing
  await page.waitForURL(/\/app\/billing/, { timeout: 10000 });
  await page.waitForLoadState("networkidle");

  // Verify the billing page content is visible
  const heading = page.locator("h1");
  await expect(heading).toContainText("Billing");
});

test("app billing page: back to chat link works", async ({ page }) => {
  // Open a case
  const { caseId } = await openCaseViaApi();

  // Navigate directly to /app/billing
  await page.goto("/app/billing");
  await page.waitForLoadState("networkidle");

  // Verify the page loaded
  await expect(page.locator("h1")).toContainText("Billing");

  // Verify the back link exists and links to /app
  const backLink = page.locator('a[href="/app"]');
  await expect(backLink).toBeVisible();
  await expect(backLink).toContainText("Back to chat");

  // Click the back link
  await backLink.click();

  // Should navigate back to /app (the root, not a specific case)
  await page.waitForURL(/\/app/, { timeout: 10000 });
});
