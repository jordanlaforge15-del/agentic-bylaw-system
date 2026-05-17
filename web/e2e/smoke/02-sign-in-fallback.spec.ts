// Smoke: the dev-fallback auth path lets a test reach /cases without
// real Clerk credentials. When CLERK_SECRET_KEY is unset, the Next.js
// proxy forwards X-Test-User-Id: demo-user-1 to the FastAPI test
// server, and /cases server-renders the seeded user's case list.
//
// The seed script always provisions the demo user before the suite
// runs (see scripts/e2e-up.sh), so /cases must NOT 401 — it should
// either show "My cases" (with seeded rows) or "No cases yet". A 401
// surfaces here as the "Sign in to view your cases" copy.

import { expect, test } from "../fixtures/test-env";

test("/cases is reachable via X-Test-User-Id fallback", async ({ page }) => {
  await page.goto("/cases");
  await expect(
    page.getByRole("heading", { level: 1, name: /My cases/ }),
  ).toBeVisible();
  await expect(
    page.getByText(/Sign in to view your cases/),
  ).toHaveCount(0);
});
