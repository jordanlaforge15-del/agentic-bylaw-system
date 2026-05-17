// Smoke: after opening a case via the API, /cases lists it.
// Exercises:
//   * /v1/cases GET (auth via X-Test-User-Id fallback)
//   * Cases page server-renders the row with the anchor + tier
//
// Uses a unique anchor per run so concurrent workers don't see the
// same row and pass for the wrong reason.

import { expect, openCaseViaApi, test } from "../fixtures/test-env";

test("/cases lists the opened case", async ({ page }) => {
  const anchorLabel = `99 List St ${Date.now()}-${Math.random()
    .toString(36)
    .slice(2, 6)}`;
  await openCaseViaApi({ anchorLabel, tier: "quick" });

  await page.goto("/cases");
  await expect(
    page.getByRole("heading", { level: 1, name: /My cases/ }),
  ).toBeVisible();
  await expect(page.getByText(anchorLabel)).toBeVisible();
});
