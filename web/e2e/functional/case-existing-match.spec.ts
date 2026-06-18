// Functional: opening a case with an anchor that already exists in
// the 30-day window surfaces the "EXISTING CASE FOUND" banner with a
// Continue button. The match lookup runs on the anchor input's onBlur.
//
// ABS-324: continuing an existing case is a Conversation-product entry,
// gated behind ADVISOR_CONVERSATION_ENTRY_ENABLED (Answers-only at launch).
// The e2e server enables it (scripts/e2e-up.sh) so this legacy path runs
// against real behavior. The form skips the anchor match-lookup until the
// menu's conversation_enabled flag settles, so we wait for the question
// menu to load before blurring (otherwise the lookup races the flag).

import { expect, openCaseViaApi, test } from "../fixtures/test-env";

test("existing-case match banner appears for a re-open attempt", async ({
  page,
}) => {
  const anchor = `dup-anchor-${Date.now()}`;
  await openCaseViaApi({ anchorLabel: anchor });

  await page.goto("/cases/new");
  await expect(page.getByTestId("question-menu")).toBeVisible();
  const anchorInput = page.getByPlaceholder(/1234 Main St, Halifax/);
  await anchorInput.fill(anchor);
  await anchorInput.blur();

  await expect(page.getByText(/EXISTING CASE FOUND/)).toBeVisible();
  await expect(
    page.getByRole("button", { name: /Continue case/ }),
  ).toBeVisible();
});
