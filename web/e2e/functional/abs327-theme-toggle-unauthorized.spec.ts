// ABS-327: Verify the theme toggle is hidden on the unauthorized home page.
//
// The light/dark mode slider should only appear when users are signed in.
// On the unauthorized (marketing) pages, the theme toggle is not available.

import { expect, test } from "../fixtures/test-env";

test.describe("theme toggle visibility (ABS-327)", () => {
  test("theme toggle is hidden on unauthorized home page", async ({
    page,
  }) => {
    await page.goto("/");

    // The theme toggle button should not be visible to unauthorized users
    const themeToggle = page.getByRole("button", {
      name: /toggle light.*dark/i,
    });
    await expect(themeToggle).not.toBeVisible();
  });
});
