// Smoke: /admin/invites is gated for the non-admin demo user.
// The page renders a deny / redirect rather than the live admin table.
//
// In the test stack the admin Clerk allowlist
// (ADVISOR_ADMIN_CLERK_USER_IDS) is empty by default, so the seeded
// demo-user-1 cannot reach admin endpoints. We don't test the
// happy-path admin view here — that's behind feature-flag plumbing
// that lives in functional, not smoke.

import { expect, test } from "../fixtures/test-env";

test("/admin/invites denies the non-admin demo user", async ({ page }) => {
  const response = await page.goto("/admin/invites");
  // Either the page returns a 4xx, redirects to /, or renders an
  // explicit "not allowed" body. We accept any of those as a pass.
  const status = response?.status() ?? 0;
  if (status >= 400) {
    return;
  }
  const url = new URL(page.url());
  if (url.pathname !== "/admin/invites") {
    // Redirected away — fine.
    return;
  }
  // Stayed on the page; the body must show a deny state, not the
  // invites table.
  await expect(
    page.getByText(/Not allowed|forbidden|sign in|not an admin/i),
  ).toBeVisible();
});
