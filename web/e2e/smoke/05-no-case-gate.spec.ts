// Smoke: visiting /app with no ?case_id= shows the gate copy and
// hides the composer. The gate steers users to /cases/new instead of
// letting them type a message that would 400 from the backend.
//
// Regression context: an earlier bug let the composer render even
// without a case, producing a confusing "case_id_required" 400 once
// the user pressed Send. The gate must always be the first thing
// they see in that state.

import { expect, test } from "../fixtures/test-env";

test("composer is gated when no case is active", async ({ page }) => {
  await page.goto("/app");
  await expect(
    page.getByRole("link", { name: /open a case/ }),
  ).toBeVisible();
  await expect(
    page.getByPlaceholder(/Ask about this parcel/),
  ).toHaveCount(0);
});
