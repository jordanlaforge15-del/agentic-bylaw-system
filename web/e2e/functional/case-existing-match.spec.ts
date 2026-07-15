// Functional: anchoring to an address that already has a case surfaces the
// always-on in-window match block (ABS-385). The conversation product is the
// primary surface now, so there is no `conversation_enabled` gate — the match
// block renders whenever a case exists for the anchor, with the designed copy
// and a "Continue conversation →" button. The match lookup runs on the anchor
// input's onBlur.

import { expect, openCaseViaApi, test } from "../fixtures/test-env";

test("existing-conversation match block appears for a re-open attempt", async ({
  page,
}) => {
  const anchor = `dup-anchor-${Date.now()}`;
  await openCaseViaApi({ anchorLabel: anchor });

  await page.goto("/cases/new");
  const anchorInput = page.getByPlaceholder(/1234 Main St, Halifax/);
  await anchorInput.fill(anchor);
  await anchorInput.blur();

  const match = page.getByTestId("existing-case-match");
  await expect(match).toBeVisible();
  await expect(match).toContainText("EXISTING CASE FOUND");
  await expect(match).toContainText(
    "You have a conversation for this address.",
  );
  await expect(
    match.getByRole("button", { name: /Continue conversation/ }),
  ).toBeVisible();
});
