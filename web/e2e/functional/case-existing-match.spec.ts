// Functional: opening a case with an anchor that already exists in
// the 30-day window surfaces the "EXISTING CASE FOUND" banner with a
// Continue button. The match lookup runs on the anchor input's onBlur.

import { expect, openCaseViaApi, test } from "../fixtures/test-env";

test("existing-case match banner appears for a re-open attempt", async ({
  page,
}) => {
  const anchor = `dup-anchor-${Date.now()}`;
  await openCaseViaApi({ anchorLabel: anchor });

  await page.goto("/cases/new");
  const anchorInput = page.getByPlaceholder(/1234 Main St, Halifax/);
  await anchorInput.fill(anchor);
  await anchorInput.blur();

  await expect(page.getByText(/EXISTING CASE FOUND/)).toBeVisible();
  await expect(
    page.getByRole("button", { name: /Continue case/ }),
  ).toBeVisible();
});
