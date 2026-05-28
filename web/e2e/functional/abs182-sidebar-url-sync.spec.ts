// Functional: clicking a case in the sidebar updates the URL case_id.
//
// Regression context (ABS-182): selectSession() loaded the new case's
// content into state but never called router.replace(), so the URL stayed
// on the originally-loaded case_id. Reloads and shared links then
// re-opened the wrong case.

import { expect, openCaseViaApi, test } from "../fixtures/test-env";

test("sidebar case click updates URL case_id", async ({ page }) => {
  // Open two distinct cases so the sidebar has at least two entries.
  const ts = Date.now();
  const { caseId: caseA } = await openCaseViaApi({
    anchorLabel: `9001 Alpha Ave ${ts}, Halifax`,
  });
  const { caseId: caseB } = await openCaseViaApi({
    anchorLabel: `9002 Beta Blvd ${ts}, Halifax`,
  });

  // Land on case A.
  await page.goto(`/app?case_id=${caseA}`);

  // Confirm the URL reflects case A.
  await expect(page).toHaveURL(new RegExp(`case_id=${caseA}`));

  // Find case B's sidebar button and click it.
  const sidebar = page.locator("aside").first();
  const caseBButton = sidebar.getByRole("button", {
    name: new RegExp(`Beta Blvd ${ts}`, "i"),
  });
  await expect(caseBButton).toBeVisible({ timeout: 8_000 });
  await caseBButton.click();

  // URL must update to case B's id without a full page reload.
  await expect(page).toHaveURL(new RegExp(`case_id=${caseB}`), {
    timeout: 5_000,
  });

  // URL must NOT still contain case A's id.
  const url = page.url();
  expect(url).not.toMatch(new RegExp(`case_id=${caseA}($|&)`));
});
