// Functional: the persistent "research tool — not legal advice" disclaimer
// is visible on the /app chat page regardless of whether a case is active.

import { expect, openCaseViaApi, test } from "../fixtures/test-env";

test("chat page shows persistent disclaimer without a case", async ({
  page,
}) => {
  await page.goto("/app");
  const disclaimer = page.getByTestId("chat-disclaimer");
  await expect(disclaimer).toBeVisible();
  await expect(disclaimer).toContainText("not legal advice");
});

test("chat page shows persistent disclaimer with an active case", async ({
  page,
}) => {
  const { caseId } = await openCaseViaApi();
  await page.goto(`/app?case_id=${caseId}`);
  const disclaimer = page.getByTestId("chat-disclaimer");
  await expect(disclaimer).toBeVisible();
  await expect(disclaimer).toContainText("Research tool only");
  await expect(disclaimer).toContainText("municipal planning office");
});
