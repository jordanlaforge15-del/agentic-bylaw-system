// Smoke: open a case via the marketing form, land on /app with the
// case_id bound. Exercises:
//   * /api/cases/classify (the pre-flight Haiku call — mock returns
//     "standard" with 0.85 confidence by default)
//   * /api/cases POST (real DB write: advisor_user + advisor_case +
//     advisor_case_credit row state transition to "reserved")
//   * Next.js redirect to /app?case_id=N
//
// Doesn't send a chat message — that lives in 04-chat-sse.spec.ts so
// each smoke spec stays focused and a failure points at one seam.

import { expect, test } from "../fixtures/test-env";

test("open a case from /cases/new", async ({ page }) => {
  await page.goto("/cases/new");

  const anchor = `123 Smoke St ${Date.now()}`;
  await page
    .getByPlaceholder(/1234 Main St, Halifax/)
    .fill(anchor);

  await page
    .getByPlaceholder(/Describe the inquiry/)
    .fill("Can I add a backyard suite at this address?");

  // Optional classifier preview — the dispatcher returns standard/0.85.
  await page.getByRole("button", { name: /Get tier recommendation/ }).click();
  await expect(
    page.getByText(/CLASSIFIER RECOMMENDS · 85% CONFIDENCE/),
  ).toBeVisible();

  await page.getByRole("button", { name: /^Open case$/ }).click();

  // We're redirected to /app?case_id=N. Wait for the URL to settle.
  await page.waitForURL(/\/app\?case_id=\d+/);
  await expect(page).toHaveURL(/case_id=\d+/);

  // The product shell shows the "Connected ·" system banner.
  await expect(page.getByText(/Connected · Regional Centre LUB/)).toBeVisible();
});
