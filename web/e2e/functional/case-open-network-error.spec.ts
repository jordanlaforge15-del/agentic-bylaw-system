// Functional: a network failure on POST /api/cases (Safari "Load failed",
// dropped connection, DNS error) surfaces an actionable error message in
// the case-open form instead of leaving the "Open case" button stuck on
// "Opening case…" with no feedback.
//
// Regression for ABS-9 (Linear): prod user norman.stephanie@gmail.com hit
// "Network error: Load failed" on Safari and was given no recovery path —
// the fetch().catch was missing, so the rejection bubbled into the
// finally{} that re-enabled the button but the surrounding code never
// surfaced the error to the user. The /api/cases fetch is now wrapped in
// a try/catch that sets the form's error banner; this spec pins that
// behaviour.
//
// Implementation: Playwright's route.abort("failed") tells the browser to
// reject the fetch with the same NS_ERROR_NET_RESET / NetworkError shape
// Safari produces on a dropped connection. The frontend has no way to
// tell the simulated abort from a real one, so this is the cleanest
// in-browser reproduction of the original bug.

import { expect, test } from "../fixtures/test-env";

test("fetch failure on POST /api/cases shows a network-error banner", async ({
  page,
}) => {
  await page.goto("/cases/new");

  // Force every POST /api/cases to fail at the network layer.
  await page.route("**/api/cases", (route) => {
    if (route.request().method() === "POST") {
      return route.abort("failed");
    }
    return route.continue();
  });

  const anchor = `network-err-${Date.now()}-${Math.random()
    .toString(36)
    .slice(2, 8)}`;
  await page.getByPlaceholder(/1234 Main St, Halifax/).fill(anchor);
  await page
    .getByPlaceholder(/Describe the inquiry/)
    .fill("Triggering a network failure on case open.");

  const openBtn = page.getByRole("button", { name: /^Open case$/ });
  await openBtn.click();

  // The new try/catch produces a message starting with "Network error
  // opening case:". Without the fix the form sat indefinitely with no
  // visible error.
  await expect(page.getByText(/Network error opening case/i)).toBeVisible({
    timeout: 5_000,
  });

  // And the button is no longer stuck on "Opening case…" — the finally
  // block returns `working` to "idle" so the user can retry without a
  // page reload.
  await expect(openBtn).toBeEnabled();
  await expect(openBtn).toHaveText(/^Open case$/);

  // The URL stayed on /cases/new (we did NOT navigate into /app).
  expect(new URL(page.url()).pathname).toBe("/cases/new");
});
