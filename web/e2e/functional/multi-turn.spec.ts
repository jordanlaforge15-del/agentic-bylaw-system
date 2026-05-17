// Functional: two consecutive messages on the same session each
// stream a reply, and the thread keeps a sensible turn count.
//
// Regression context: a prior session-resume bug caused the second
// turn to 404 because of a user_id mismatch between the in-memory
// chat session and the DB-resolved user. The smoke chat test only
// sends one message, so this is the dedicated multi-turn coverage.

import { expect, openCaseViaApi, test } from "../fixtures/test-env";

test("two consecutive turns both stream replies", async ({ page }) => {
  const { caseId } = await openCaseViaApi();
  await page.goto(`/app?case_id=${caseId}`);

  const textarea = page.getByPlaceholder(/Ask about this parcel/);
  const sendBtn = page.getByRole("button", { name: /^Send/ });
  const thread = page.getByTestId("chat-thread");

  await textarea.fill("First question about the bylaw.");
  await sendBtn.click();
  await expect(thread).toContainText(/Based on the bylaw evidence/i, {
    timeout: 15_000,
  });

  // Second turn. The composer should still be enabled after the
  // first turn settles.
  await expect(textarea).toBeEnabled();
  await textarea.fill("Follow-up question on the same case.");
  await sendBtn.click();

  // The thread should now contain at least two assistant replies
  // matching the deterministic mock. We count substring occurrences
  // rather than splitting on DOM nodes — markdown renders to nested
  // elements that could change.
  await expect(async () => {
    const text = (await thread.innerText()).toLowerCase();
    const occurrences = text.split("based on the bylaw evidence").length - 1;
    expect(occurrences).toBeGreaterThanOrEqual(2);
  }).toPass({ timeout: 20_000 });
});
