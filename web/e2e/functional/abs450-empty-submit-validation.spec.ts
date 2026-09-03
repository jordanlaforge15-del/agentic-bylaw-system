// Functional: an incomplete /cases/new never fails silently (ABS-450).
//
// Before this fix the CTA was already wired to no-op on an empty anchor, but
// nothing told the user: `Btn` had no disabled styling, so the button looked
// fully live while swallowing the click — no request, no error, no visual
// change. A user reasonably concluded the app was broken.
//
// The contract pinned here:
//   * the CTA is disabled until BOTH the anchor and the question have content
//   * a disabled CTA *reads* disabled (dimmed, not-allowed cursor)
//   * a visible hint names whichever field is still missing
//   * clicking while incomplete fires no /api/cases request (and still leaves
//     the user with an on-screen explanation, not silence)
//   * completing the form clears the hint and enables the CTA

import { expect, test } from "../fixtures/test-env";

test("the empty form disables the CTA, explains why, and posts nothing", async ({
  page,
}) => {
  // Count every case-open attempt, matching the ticket's fetch interceptor.
  const caseOpenAttempts: string[] = [];
  await page.route("**/api/cases", (route) => {
    caseOpenAttempts.push(route.request().method());
    return route.continue();
  });

  await page.goto("/cases/new");

  const cta = page.getByTestId("start-conversation-btn");
  const hint = page.getByTestId("start-conversation-hint");

  // Both fields empty: the affordance matches the behaviour.
  await expect(cta).toBeDisabled();
  await expect(hint).toHaveText(
    "Add a property address and your question to start.",
  );
  await expect(cta).toHaveCSS("opacity", "0.5");
  await expect(cta).toHaveCSS("cursor", "not-allowed");

  // Clicking anyway (force past the disabled affordance) fires no request and
  // leaves the explanation on screen — never a blank no-op.
  await cta.click({ force: true });
  expect(caseOpenAttempts).toEqual([]);
  await expect(hint).toBeVisible();
  expect(new URL(page.url()).pathname).toBe("/cases/new");
});

test("the hint tracks which field is still missing", async ({ page }) => {
  await page.goto("/cases/new");

  const cta = page.getByTestId("start-conversation-btn");
  const hint = page.getByTestId("start-conversation-hint");
  const anchorInput = page.getByPlaceholder(/1234 Main St, Halifax/);
  const questionInput = page.getByPlaceholder(/Ask your question/);

  // Anchor only — the question is what's missing now.
  await anchorInput.fill(`450 Validation Ave ${Date.now()}`);
  await expect(cta).toBeDisabled();
  await expect(hint).toHaveText("Add your question to start.");

  // Question only — the anchor is what's missing.
  await anchorInput.fill("");
  await questionInput.fill("Can I add a basement apartment?");
  await expect(cta).toBeDisabled();
  await expect(hint).toHaveText("Add a property address to start.");

  // Both present — hint clears, CTA goes live and reads live.
  await anchorInput.fill(`450 Validation Ave ${Date.now()}`);
  await expect(cta).toBeEnabled();
  await expect(hint).toHaveCount(0);
  await expect(cta).toHaveCSS("opacity", "1");

  // Whitespace-only input does not count as content.
  await questionInput.fill("   ");
  await expect(cta).toBeDisabled();
  await expect(hint).toHaveText("Add your question to start.");
});
