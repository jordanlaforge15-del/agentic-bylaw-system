// Smoke: /admin/invites is gated for non-admin users.
// The page renders a deny / redirect rather than the live admin table.
//
// The e2e Clerk mock checks ADVISOR_ADMIN_CLERK_USER_IDS (set to
// demo-user-1 in e2e-up.sh). This test signs in as a DIFFERENT user
// so the admin gate denies access — proving the allowlist check works.

import { expect, test } from "../fixtures/test-env";

const E2E_BASE_URL =
  process.env.E2E_BASE_URL || "http://localhost:3001";

test("/admin/invites denies a non-admin user", async ({
  context,
  page,
}) => {
  // Override the authedContext's demo-user-1 identity with a user
  // that is NOT on the admin allowlist.
  const url = new URL(E2E_BASE_URL);
  await context.addCookies([
    {
      name: "abs_test_sub_user_id",
      value: "non-admin-smoke-user",
      domain: url.hostname,
      path: "/",
      httpOnly: false,
      secure: false,
      sameSite: "Lax",
    },
  ]);

  const response = await page.goto("/admin/invites");
  // Either the page returns a 4xx, redirects away, or renders an
  // explicit "not allowed" body. We accept any of those as a pass.
  const status = response?.status() ?? 0;
  if (status >= 400) {
    return;
  }
  const pageUrl = new URL(page.url());
  if (pageUrl.pathname !== "/admin/invites") {
    return;
  }
  await expect(
    page.getByText(/Not allowed|forbidden|sign in|not an admin/i),
  ).toBeVisible();
});
