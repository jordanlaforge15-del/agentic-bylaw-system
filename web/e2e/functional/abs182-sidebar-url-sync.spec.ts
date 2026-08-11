// Functional: clicking a case in the sidebar updates the URL case_id.
//
// Regression context (ABS-182): selectSession() loaded the new case's
// content into state but never called router.replace(), so the URL stayed
// on the originally-loaded case_id. Reloads and shared links then
// re-opened the wrong case.

import {
  expect,
  expectCaseIdInUrl,
  openCaseViaApi,
  test,
} from "../fixtures/test-env";

test("sidebar case click updates URL case_id", async ({ page }) => {
  const ts = Date.now();
  const firstMessage = "What is the minimum front yard setback?";

  // Create case A and seed its session by navigating with first_message.
  const anchorA = `9001 Alpha Ave ${ts}, Halifax`;
  const { caseId: caseA } = await openCaseViaApi({ anchorLabel: anchorA });
  await page.goto(
    `/app?case_id=${caseA}&first_message=${encodeURIComponent(firstMessage)}`,
  );
  await expect(page.getByTestId("chat-thread")).toContainText(
    /Based on the bylaw evidence/i,
    { timeout: 15_000 },
  );

  // Create case B and seed its session the same way.
  const anchorB = `9002 Beta Blvd ${ts}, Halifax`;
  const { caseId: caseB } = await openCaseViaApi({ anchorLabel: anchorB });
  await page.goto(
    `/app?case_id=${caseB}&first_message=${encodeURIComponent(firstMessage)}`,
  );
  await expect(page.getByTestId("chat-thread")).toContainText(
    /Based on the bylaw evidence/i,
    { timeout: 15_000 },
  );

  // Navigate to case A — the sidebar loads all sessions, so case B appears.
  await page.goto(`/app?case_id=${caseA}`);
  await expect(page).toHaveURL(new RegExp(`case_id=${caseA}`));

  // Find case B's sidebar button (title contains the anchor label).
  const sidebar = page.locator("aside").first();
  const caseBButton = sidebar.getByRole("button", {
    name: new RegExp(escapeRegExp(`Beta Blvd ${ts}`), "i"),
  });
  await expect(caseBButton).toBeVisible({ timeout: 8_000 });
  await caseBButton.click();

  // URL must update to case B's id without a full page reload.
  await expectCaseIdInUrl(page, caseB);

  // URL must NOT still contain case A's id.
  expect(page.url()).not.toMatch(new RegExp(`case_id=${caseA}($|&)`));
});

function escapeRegExp(s: string): string {
  return s.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
}
