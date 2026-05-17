// Smoke: marketing landing renders + primary nav is reachable.
//
// Runs across all four viewport projects (see playwright.config.ts).
// The landing copy is in two parts — a kicker mono caption ("HRM ·
// PRIVATE BETA · MAY 2026") and the headline ("An expert planner..."),
// split across multiple <br/>s. We assert on the kicker + the
// "planner" word + the two CTAs.

import { expect, test } from "../fixtures/test-env";

test.describe("marketing landing", () => {
  test("renders hero + primary CTAs", async ({ page }) => {
    await page.goto("/");
    // The kicker has two variants: a short one visible only on lg+,
    // and the long "HRM · PRIVATE BETA · MAY 2026" version that's
    // always visible. Match the long version so the assertion holds
    // across every viewport project.
    await expect(
      page.getByText(/HRM · PRIVATE BETA · MAY/),
    ).toBeVisible();
    await expect(
      page.getByRole("heading", { level: 1 }).filter({ hasText: /planner/i }),
    ).toBeVisible();
    await expect(
      page.getByRole("link", { name: /Get an invite/ }).first(),
    ).toBeVisible();
    await expect(
      page.getByRole("link", { name: /See pricing/ }).first(),
    ).toBeVisible();
  });
});
