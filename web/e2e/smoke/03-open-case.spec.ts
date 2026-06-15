// Smoke: the /cases/new entry flow renders the priced-question catalog
// (ABS-320) and the free case container reaches the chat product.
//
// ABS-320 migrated this surface off the quick/standard/complex tier model
// onto question selection: the form shows the live question menu and sells
// per-question answers. A case is a free container, so the path into the
// chat product is the in-window match's "Continue case" button. This smoke
// pins:
//   * the question menu renders from GET /api/billing/questions
//   * continuing an existing (free) case lands on /app?case_id=N with the
//     product shell connected
//
// The money paths (intake -> checkout, quote -> buy) need a live payment
// processor and are exercised against the real services by abs315/abs316;
// the menu rendering + entry wiring is what this smoke guards.

import { expect, openCaseViaApi, test } from "../fixtures/test-env";

test("the case-open form shows the question menu and continues into chat", async ({
  page,
}) => {
  // Seed a free case container so the in-window match path has a target.
  const anchor = `123 Smoke St ${Date.now()}`;
  const { caseId } = await openCaseViaApi({ anchorLabel: anchor });

  await page.goto("/cases/new");

  // The priced-question menu renders (the migrated entry flow).
  await expect(page.getByTestId("question-menu")).toBeVisible();
  await expect(
    page.getByTestId("question-option-permitted_use"),
  ).toBeVisible();

  // Anchoring to the existing case surfaces the free "continue" path.
  await page.getByPlaceholder(/1234 Main St, Halifax/).fill(anchor);
  await page.getByPlaceholder(/1234 Main St, Halifax/).blur();
  await expect(page.getByText(/EXISTING CASE FOUND/)).toBeVisible();
  await page.getByRole("button", { name: /Continue case/ }).click();

  // We're redirected into the chat product with the case bound.
  await page.waitForURL(/\/app\?case_id=\d+/);
  await expect(page).toHaveURL(new RegExp(`case_id=${caseId}\\b`));

  // The product shell shows the "Connected ·" system banner.
  await expect(page.getByText(/Connected · Regional Centre LUB/)).toBeVisible();
});
