// Functional: /pricing displays "reasoning steps" terminology in tier blurbs
// instead of "retrieval rounds" to match what users see in the UI.
// ABS-185: tier descriptions should match the reasoning-steps disclosure.

import { expect, test } from "../fixtures/test-env";

test("pricing page shows reasoning steps terminology", async ({
  page,
}) => {
  await page.goto("/pricing");

  // Verify that the page contains "reasoning steps" terminology
  await expect(page.locator("body")).toContainText("reasoning steps");

  // Verify there are NO instances of "retrieval rounds" on the pricing page
  const pageContent = await page.textContent("body");
  if (pageContent) {
    expect(pageContent).not.toContain("retrieval rounds");
  }
});
