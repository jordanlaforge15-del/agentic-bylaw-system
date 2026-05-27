// "Open full reading →" CTA on the home page Try-It widget navigates to
// /cases/new with the address and question pre-populated. ABS-180.

import { expect, test } from "../fixtures/test-env";

test.describe("home page Try-It — Open full reading → CTA", () => {
  test("button navigates to /cases/new with address and question pre-filled", async ({
    page,
  }) => {
    await page.goto("/");

    // Trigger the 5184 Morris St sample reading.
    await page.getByRole("button", { name: "5184 Morris St" }).click();

    // Wait for the verdict card to appear.
    await expect(page.getByText(/35%/)).toBeVisible({ timeout: 10_000 });

    // Click the CTA.
    await page.getByRole("button", { name: /Open full reading/ }).click();

    // Should land on /cases/new with the anchor and first_message params.
    await expect(page).toHaveURL(/\/cases\/new/);
    await expect(page).toHaveURL(/anchor_label=5184/);
    await expect(page).toHaveURL(/first_message=/);

    // The anchor input should be pre-populated with the address.
    const anchorInput = page.getByPlaceholder(/1234 Main St/);
    await expect(anchorInput).toHaveValue(/5184 Morris St/);
  });

  test("button navigates correctly for a different sample address", async ({
    page,
  }) => {
    await page.goto("/");

    await page.getByRole("button", { name: "2310 Gottingen St" }).click();
    await expect(page.getByText(/None required/)).toBeVisible({ timeout: 10_000 });

    await page.getByRole("button", { name: /Open full reading/ }).click();

    await expect(page).toHaveURL(/\/cases\/new/);
    await expect(page).toHaveURL(/anchor_label=2310/);
  });
});
