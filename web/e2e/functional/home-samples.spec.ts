// Verifies that the home page "Try It" demo section and the ProofGrid display
// the verified pre-recorded sample data, not placeholder or fabricated values.
// ABS-83, ABS-179.

import { expect, test } from "../fixtures/test-env";

test.describe("home page — pre-recorded samples", () => {
  test.beforeEach(async ({ page }) => {
    await page.goto("/");
  });

  test("TryDemo shows only the three preset sample address buttons (no free-text input)", async ({
    page,
  }) => {
    // Widget must not have a free-text input — only the three preset buttons.
    await expect(page.locator('input[placeholder*="Morris"]')).not.toBeVisible();
    await expect(page.getByRole("button", { name: "5184 Morris St" })).toBeVisible();
    await expect(
      page.getByRole("button", { name: "2310 Gottingen St" }),
    ).toBeVisible();
    await expect(page.getByRole("button", { name: "1208 Robie St" })).toBeVisible();
  });

  test("clicking a preset chip shows SAMPLE READING badge (not VERIFIED · 0.93 CONF)", async ({
    page,
  }) => {
    await page.getByRole("button", { name: "5184 Morris St" }).click();
    // Wait for the thinking sequence to finish and the verdict to appear.
    await expect(page.getByText(/35%/)).toBeVisible({ timeout: 10_000 });
    // Must show "SAMPLE READING", not the misleading "VERIFIED · 0.93 CONF".
    await expect(page.getByText("SAMPLE READING")).toBeVisible();
    await expect(page.getByText(/VERIFIED.*CONF/)).not.toBeVisible();
    // § 7 appears in both the verdict card and the ProofGrid; just assert at least one is visible.
    await expect(page.getByText(/§ 7/).first()).toBeVisible();
  });

  test("ProofGrid shows verified answers for all six samples", async ({
    page,
  }) => {
    // Scope to the ProofGrid section to avoid conflicts with other page regions.
    const grid = page.locator("text=REAL READINGS").locator("..").locator("..");
    // Lot coverage — ER-1
    await expect(page.getByText("35%.")).toBeVisible();
    // Height — HR-1
    await expect(page.getByText("20.0 m by-right.")).toBeVisible();
    // Parking — CEN-1
    await expect(page.getByText("None required.")).toBeVisible();
    // Combination of uses — ER-2
    await expect(page.getByText("Both permitted concurrently.")).toBeVisible();
    // Single-unit — ER-1
    await expect(page.getByText("Permitted as-of-right.")).toBeVisible();
    // Multi-unit — CEN-1 (exact match scoped to the grid card)
    await expect(grid.getByText("Permitted.", { exact: true })).toBeVisible();
  });

  test("ProofGrid shows verified citation references", async ({ page }) => {
    await expect(page.getByText("HRM LUB § 120(b)")).toBeVisible();
    await expect(page.getByText("HRM LUB § 49")).toBeVisible();
  });
});
