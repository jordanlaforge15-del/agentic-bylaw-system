// Functional: the conversation-first /cases/new tolerates a network failure
// on the anchor match-lookup without crashing or wedging the UI (ABS-385).
//
// The always-reachable network call before the user commits is the on-blur
// in-window match lookup (GET /api/cases/match). A dropped connection (Safari
// "Load failed") must degrade to "no match", and the free CTA + question must
// stay usable — the failure can't block opening a conversation.

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

  // No match block (the failed lookup is treated as "no match"), and the
  // form did not crash: the free CTA is enabled and the question composer is
  // interactive.
  await expect(page.getByTestId("existing-case-match")).toHaveCount(0);
  await page
    .getByPlaceholder(/Ask your question/)
    .fill("Can I add a second unit?");
  await expect(page.getByTestId("start-conversation-btn")).toBeEnabled();

  // We stayed on /cases/new.
  expect(new URL(page.url()).pathname).toBe("/cases/new");
});
