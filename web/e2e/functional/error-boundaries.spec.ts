// Functional: verify that the root error boundary and custom 404 page
// render user-friendly fallback UI instead of a blank screen.

import { expect, test } from "../fixtures/test-env";

test.describe("error boundaries", () => {
  test("404: navigating to an unknown route renders the not-found page", async ({
    page,
  }) => {
    await page.goto("/this-route-does-not-exist-e2e");

    await expect(page.getByText("404")).toBeVisible();
    await expect(
      page.getByRole("heading", { name: /page not found/i }),
    ).toBeVisible();
    await expect(
      page.getByRole("link", { name: /back to home/i }),
    ).toBeVisible();
  });

  test("error boundary: unhandled render error shows fallback UI with retry", async ({
    page,
  }) => {
    // The /e2e-throw page throws during render, triggering error.tsx.
    await page.goto("/e2e-throw");

    await expect(
      page.getByRole("heading", { name: /unexpected error/i }),
    ).toBeVisible({ timeout: 10_000 });
    await expect(page.getByText(/something went wrong/i)).toBeVisible();
    await expect(
      page.getByRole("button", { name: /try again/i }),
    ).toBeVisible();
    await expect(
      page.getByRole("link", { name: /home/i }),
    ).toBeVisible();
  });

  test("404: back-to-home link navigates to the landing page", async ({
    page,
  }) => {
    await page.goto("/nonexistent-path-e2e-test");

    const homeLink = page.getByRole("link", { name: /back to home/i });
    await expect(homeLink).toBeVisible();
    await homeLink.click();
    await page.waitForURL("/");
  });
});
