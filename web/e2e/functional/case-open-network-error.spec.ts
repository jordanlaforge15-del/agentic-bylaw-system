// Functional: the case-open form tolerates a network failure on the
// anchor match-lookup without crashing or wedging the UI.
//
// History: this spec originally guarded POST /api/cases (the old
// tier-credit "Open case" button) against a silent hang on a dropped
// connection (ABS-9, Safari "Load failed"). ABS-320 migrated the form off
// the tier model onto the priced-question catalog, so that button and its
// POST are gone. The always-reachable network call in the migrated form is
// the on-blur anchor match-lookup (GET /api/cases/match); this spec pins
// that a failed lookup degrades gracefully (treated as "no match") and the
// form stays usable — the question menu still renders and is interactive.

import { expect, test } from "../fixtures/test-env";

test("a failed anchor match-lookup degrades gracefully", async ({ page }) => {
  await page.goto("/cases/new");

  // Force the match-lookup to fail at the network layer.
  await page.route("**/api/cases/match*", (route) => route.abort("failed"));

  const anchor = `network-err-${Date.now()}-${Math.random()
    .toString(36)
    .slice(2, 8)}`;
  const anchorInput = page.getByPlaceholder(/1234 Main St, Halifax/);
  await anchorInput.fill(anchor);
  await anchorInput.blur();

  // No "existing case" banner is shown (the failed lookup is treated as
  // "no match"), and crucially the form did not crash: the question menu
  // is still rendered and interactive.
  await expect(page.getByText(/EXISTING CASE FOUND/)).toHaveCount(0);
  await expect(page.getByTestId("question-menu")).toBeVisible();
  await page.getByTestId("question-option-permitted_use").click();
  await expect(page.getByText(/Describe your situation/i)).toBeVisible();

  // We stayed on /cases/new.
  expect(new URL(page.url()).pathname).toBe("/cases/new");
});
