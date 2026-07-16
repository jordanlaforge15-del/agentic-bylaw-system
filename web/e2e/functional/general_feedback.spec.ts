// Functional: global general-feedback widget (ABS-215).
//
// The floating "Feedback" launcher must be available on every authorized
// product page (pages only viewable when logged in), open a single free-text
// box with NO category selection, gate submission until text is entered, and
// confirm + persist a submission. It must NOT appear on public marketing
// pages.
//
// The categorized sidebar widget (ABS-129) is covered separately in
// general-feedback.spec.ts; this spec targets the distinct `app-feedback-*`
// testids of the global widget.

import { expect, test } from "../fixtures/test-env";

test.describe("global general feedback (ABS-215)", () => {
  test("launcher is available across authorized pages", async ({ page }) => {
    for (const path of ["/app", "/submissions/new"]) {
      await page.goto(path);
      await expect(
        page.getByTestId("app-feedback-open"),
        `feedback launcher should be present on ${path}`,
      ).toBeVisible();
    }
  });

  test("launcher is not shown on public marketing pages", async ({ page }) => {
    await page.goto("/pricing");
    await expect(page.getByTestId("app-feedback-open")).toHaveCount(0);
  });

  test("form is a single free-text box with no category selection", async ({
    page,
  }) => {
    await page.goto("/app");
    await page.getByTestId("app-feedback-open").click();

    await expect(page.getByTestId("app-feedback-form")).toBeVisible();
    await expect(page.getByTestId("app-feedback-message")).toBeVisible();

    // ABS-215: no category/tag chips on the global widget.
    await expect(page.getByTestId("feedback-category-ux_issue")).toHaveCount(0);
    await expect(
      page.getByTestId("feedback-category-feature_request"),
    ).toHaveCount(0);
  });

  test("submit is disabled until text is entered", async ({ page }) => {
    await page.goto("/app");
    await page.getByTestId("app-feedback-open").click();

    const submit = page.getByTestId("app-feedback-submit");
    await expect(submit).toBeDisabled();

    await page.getByTestId("app-feedback-message").fill("hello");
    await expect(submit).toBeEnabled();
  });

  test("submitting feedback shows a confirmation that auto-dismisses", async ({
    page,
  }) => {
    await page.goto("/app");
    await page.getByTestId("app-feedback-open").click();

    await page
      .getByTestId("app-feedback-message")
      .fill("I would like a different pricing model.");
    await page.getByTestId("app-feedback-submit").click();

    // Form closes, acknowledgment appears.
    await expect(page.getByTestId("app-feedback-form")).not.toBeVisible();
    await expect(page.getByTestId("app-feedback-thanks")).toBeVisible();

    // Auto-dismiss after 4s (parity with ABS-212); launcher returns.
    await page.waitForTimeout(4500);
    await expect(page.getByTestId("app-feedback-thanks")).not.toBeVisible();
    await expect(page.getByTestId("app-feedback-open")).toBeVisible();
  });
});
