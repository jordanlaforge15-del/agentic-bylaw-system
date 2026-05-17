// Functional: Enter sends; Shift+Enter inserts a newline. This is a
// usability contract the composer page comment calls out explicitly,
// so we lock it down here.

import { expect, openCaseViaApi, test } from "../fixtures/test-env";

test("Shift+Enter inserts a newline, Enter sends", async ({ page }) => {
  const { caseId } = await openCaseViaApi();
  await page.goto(`/app?case_id=${caseId}`);

  const textarea = page.getByPlaceholder(/Ask about this parcel/);
  await textarea.focus();
  await textarea.type("line one");
  await page.keyboard.down("Shift");
  await page.keyboard.press("Enter");
  await page.keyboard.up("Shift");
  await textarea.type("line two");

  // Shift+Enter didn't submit — value still in textarea, contains \n.
  await expect(textarea).toHaveValue(/line one\nline two/);

  await page.keyboard.press("Enter");

  // Enter submitted — textarea clears and assistant reply streams.
  await expect(textarea).toHaveValue("");
  await expect(page.getByTestId("chat-thread")).toContainText(
    /Based on the bylaw evidence/i,
    { timeout: 15_000 },
  );
});
