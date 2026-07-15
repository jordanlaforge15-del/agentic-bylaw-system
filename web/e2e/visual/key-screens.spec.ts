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

test("pricing page matches snapshot", async ({ page }) => {
  // ABS-387 "Pay by the turn": trial grant + token top-ups + gated report
  // SKUs. The top-ups/reports/posture are fetched client-side, so wait for
  // the fully-loaded data-driven state to settle before capturing —
  // otherwise the loading skeleton (or a half-rendered catalog) races the
  // screenshot. The e2e stack enables all five report slugs, so the whole
  // reports section renders against the live gate.
  await page.goto("/pricing");
  await expect(
    page.getByRole("heading", { level: 1, name: /Pay by the turn/i }),
  ).toBeVisible();
  await expect(page.getByTestId("trial-card")).toBeVisible();
  await expect(page.getByTestId("topup-card-large")).toBeVisible();
  await expect(page.getByTestId("reports-section")).toBeVisible();
  await expect(page.getByTestId("report-sku-variance_justification")).toBeVisible();
  await expect(page).toHaveScreenshot("pricing.png", { fullPage: true });
});
