// Verifies that the home page "See what's permitted" demo section and the
// ProofGrid display the verified pre-recorded sample data, not placeholder
// or fabricated values. ABS-83.

import { expect, test } from "../fixtures/test-env";

test.describe("home page — pre-recorded samples", () => {
  test.beforeEach(async ({ page }) => {
    await page.goto("/");
  });

  test("TryDemo preset chips show the three verified sample addresses", async ({
    page,
  }) => {
    // The address demo renders three preset chips from SAMPLE_READINGS.
    await expect(page.getByRole("button", { name: "5184 Morris St" })).toBeVisible();
    await expect(
      page.getByRole("button", { name: "2310 Gottingen St" }),
    ).toBeVisible();
    await expect(page.getByRole("button", { name: "1208 Robie St" })).toBeVisible();
  });

  test("clicking a preset chip shows the verified verdict", async ({ page }) => {
    await page.getByRole("button", { name: "5184 Morris St" }).click();
    // Wait for the thinking sequence to finish and the verdict to appear.
    await expect(page.getByText(/35%/)).toBeVisible({ timeout: 10_000 });
    await expect(page.getByText(/§ 7/)).toBeVisible();
  });

  test("ProofGrid shows verified answers for all six samples", async ({
    page,
  }) => {
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
    // Multi-unit — CEN-1
    await expect(page.getByText("Permitted.")).toBeVisible();
  });

  test("ProofGrid shows verified citation references", async ({ page }) => {
    await expect(page.getByText("HRM LUB § 120(b)")).toBeVisible();
    await expect(page.getByText("HRM LUB § 49")).toBeVisible();
  });
});
