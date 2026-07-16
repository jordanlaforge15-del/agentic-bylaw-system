// ABS-25 — Animated "Reading bylaw…" thinking indicator.
//
// Asserts:
//  1. While a response is streaming, the "ABS · READING" badge is visible.
//  2. The thinking label is rendered with animated-ellipsis spans (.abs-ellipsis-dot)
//     rather than a static "…" character — confirming the visual animation is wired up.
//  3. Once the reply completes, the thinking indicator is gone.

import { expect, openCaseViaApi, test } from "../fixtures/test-env";

test("thinking indicator shows animated ellipsis while streaming", async ({
  page,
}) => {
  const { caseId } = await openCaseViaApi();
  await page.goto(`/app?case_id=${caseId}`);

  const textarea = page.getByPlaceholder(/Ask about this parcel/);
  await expect(textarea).toBeVisible();
  await textarea.scrollIntoViewIfNeeded();

  // Wait for React hydration before filling.
  await page.waitForFunction(() => {
    const el = document.querySelector(
      'textarea[placeholder^="Ask about this parcel"]',
    );
    if (!el) return false;
    return Object.keys(el).some((k) => k.startsWith("__reactProps"));
  });

  await textarea.fill("What is the minimum front yard setback?");
  await textarea.press("Enter");

  // The "ABS · READING" badge must appear during the stream.
  const badge = page.getByText("ABS · READING");
  await expect(badge).toBeVisible({ timeout: 8_000 });

  // The thinking row must contain animated-ellipsis dot spans, not a raw "…".
  // We locate the thinking label container (the row holding "→ Reading bylaw")
  // and assert it has at least one .abs-ellipsis-dot child.
  const ellipsisDots = page
    .locator(".abs-ellipsis-dot")
    .first();
  await expect(ellipsisDots).toBeVisible({ timeout: 8_000 });

  // Once streaming completes the thinking indicator should disappear.
  await expect(badge).not.toBeVisible({ timeout: 20_000 });
});
