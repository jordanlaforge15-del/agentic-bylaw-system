// Functional: /cases/new displays "reasoning steps" in tier selection
// instead of "retrieval rounds" to match UI terminology. ABS-185.

import { expect, test } from "../fixtures/test-env";

test("case open form shows reasoning steps terminology", async ({
  page,
}) => {
  await page.goto("/cases/new");

  // Verify tier selection includes "reasoning steps" terminology
  await expect(page.locator("body")).toContainText("reasoning steps");
});
