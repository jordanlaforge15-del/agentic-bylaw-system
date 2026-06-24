// ABS-336 — /cases/new is a protected route.
//
// /cases/new was moved into the (product) group (ABS-334) and renders the
// authorized shell, but proxy.ts only guarded /app(.*) and /admin(.*).
// ABS-336 adds it to isProtectedRoute so unauthenticated visitors are
// bounced to sign-in before the authorized chrome renders.

import { test, expect } from "../fixtures/test-env";

const E2E_BASE_URL =
  process.env.E2E_BASE_URL || "http://localhost:3001";

test.describe("ABS-336: /cases/new route protection", () => {
  test("unauthenticated GET /cases/new redirects to sign-in", async ({
    context,
  }) => {
    await context.clearCookies();
    const page = await context.newPage();
    await page.goto(`${E2E_BASE_URL}/cases/new`);

    // proxy.ts isProtectedRoute now covers /cases/new; Clerk mock
    // should redirect anonymous visitors to /sign-in.
    await expect(page).toHaveURL(/\/sign-in/);
  });

  test("authenticated user reaches /cases/new without redirect", async ({
    authedContext: _,
    context,
  }) => {
    // authedContext fixture sets the demo-user auth cookies.
    const page = await context.newPage();
    await page.goto(`${E2E_BASE_URL}/cases/new`);

    // Should stay on /cases/new and render the authorized shell.
    await expect(page).toHaveURL(/\/cases\/new/);
    await expect(
      page.getByRole("button", { name: "Workspace menu" }),
    ).toBeVisible();
  });
});
