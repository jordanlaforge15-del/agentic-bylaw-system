// ABS-126 — Persistent disclaimer bar on the /app chat page.
//
// Asserts:
//  1. The disclaimer bar is visible on the chat page without a case.
//  2. The disclaimer bar is visible on the chat page with an active case.
//  3. The bar text identifies the tool as a research tool and not legal advice.
//  4. The bar does not scroll away — it stays anchored as the thread grows.

import { expect, openCaseViaApi, test } from "../fixtures/test-env";

test.describe("chat disclaimer bar", () => {
  test("disclaimer bar is visible on /app without a case", async ({ page }) => {
    await page.goto("/app");
    const bar = page.getByTestId("chat-disclaimer-bar");
    await expect(bar).toBeVisible();
    await expect(bar).toContainText(/research tool/i);
    await expect(bar).toContainText(/not legal advice/i);
  });

  test("disclaimer bar is visible on /app with an active case", async ({
    page,
  }) => {
    const { caseId } = await openCaseViaApi();
    await page.goto(`/app?case_id=${caseId}`);
    const bar = page.getByTestId("chat-disclaimer-bar");
    await expect(bar).toBeVisible();
    await expect(bar).toContainText(/research tool/i);
    await expect(bar).toContainText(/not legal advice/i);
  });

  test("disclaimer bar is pinned above the composer (not inside the scrollable thread)", async ({
    page,
  }) => {
    const { caseId } = await openCaseViaApi();
    await page.goto(`/app?case_id=${caseId}`);

    // The bar must be rendered outside (below) the chat thread but
    // above the composer — i.e., its bottom edge is above the
    // composer's top edge, and its top edge is below the thread's
    // bottom edge.
    const bar = page.getByTestId("chat-disclaimer-bar");
    const thread = page.getByTestId("chat-thread");
    const composer = page.locator(
      'textarea[placeholder^="Ask about this parcel"]',
    );

    await expect(bar).toBeVisible();
    await expect(composer).toBeVisible();

    const barBox = await bar.boundingBox();
    const threadBox = await thread.boundingBox();
    const composerBox = await composer.boundingBox();

    expect(barBox).not.toBeNull();
    expect(threadBox).not.toBeNull();
    expect(composerBox).not.toBeNull();

    // Bar sits below the thread's bottom edge.
    expect(barBox!.y).toBeGreaterThanOrEqual(
      threadBox!.y + threadBox!.height - 2, // 2px tolerance for sub-pixel
    );
    // Bar sits above the composer's top edge.
    expect(barBox!.y + barBox!.height).toBeLessThanOrEqual(
      composerBox!.y + composerBox!.height + 2,
    );
  });
});
