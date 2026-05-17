// Visual: pixel-tolerant screenshot snapshots of the highest-leverage
// screens. The threshold (`toHaveScreenshot.maxDiffPixelRatio = 0.02`)
// is set in playwright.config.ts — generous enough that font hinting
// jitter doesn't flake, tight enough that a layout collapse (composer
// disappeared, sidebar overflowed, etc.) fails.
//
// First run with `npx playwright test --update-snapshots` to create
// baselines under `web/e2e/visual/key-screens.spec.ts-snapshots/`.

import { expect, openCaseViaApi, test } from "../fixtures/test-env";

test("landing page matches snapshot", async ({ page }) => {
  await page.goto("/");
  // Wait for the headline so font swap doesn't shift after capture.
  await expect(
    page.getByRole("heading", { level: 1 }).first(),
  ).toBeVisible();
  await expect(page).toHaveScreenshot("landing.png", { fullPage: false });
});

test("product chat shell matches snapshot", async ({ page }) => {
  const { caseId } = await openCaseViaApi();
  await page.goto(`/app?case_id=${caseId}`);
  await expect(
    page.getByPlaceholder(/Ask about this parcel/),
  ).toBeVisible();
  // Mask the address pill and any timestamps that drift across runs.
  await expect(page).toHaveScreenshot("app-shell.png", {
    fullPage: false,
    mask: [page.locator('[data-testid="chat-thread"] >> nth=0')],
  });
});
