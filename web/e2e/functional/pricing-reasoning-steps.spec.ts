// Functional: /pricing uses question-menu copy, not turn-pack tier copy.
//
// ABS-310 replaced the turn-pack tier matrix with a per-question menu.
// This spec (originally ABS-185) verified "reasoning steps" terminology
// in tier blurbs. Those blurbs are gone. The spec is updated to confirm
// the stale tier-model copy is absent and the question-menu kicker is
// present.

import { expect, test } from "../fixtures/test-env";

test("pricing page shows question-menu copy and not turn-pack tier copy", async ({
  page,
}) => {
  await page.goto("/pricing");

  // The page header should identify the question-menu surface.
  await expect(page.locator("body")).toContainText("PRICING · QUESTIONS");

  // Turn-pack / tier / reasoning-steps copy should be absent.
  const text = await page.textContent("body");
  if (text) {
    expect(text).not.toContain("reasoning steps");
    expect(text).not.toContain("retrieval rounds");
    expect(text).not.toContain("CASE CREDITS");
    expect(text).not.toContain("quick credit");
    expect(text).not.toContain("standard credit");
    expect(text).not.toContain("complex credit");
  }
});
