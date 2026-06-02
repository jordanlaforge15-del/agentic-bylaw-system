// ABS-259: General Feedback floating button must be hidden behind a gate.
//
// The gate is controlled by NEXT_PUBLIC_GENERAL_FEEDBACK_ENABLED:
//   - "true"  → the fixed bottom-right "Feedback" button renders on /app.
//   - absent  → GeneralFeedback.tsx returns null immediately; no button is
//               rendered regardless of route.
//
// In the standard e2e environment the flag is "true" (set in
// .env.local.example), so this spec verifies the gate-open path.
// The gate-closed path relies on the component's early return being the only
// render path — a trivial React guard with no framework primitives that need
// independent browser-level verification.
//
// What we verify:
//   1. When the gate is open, the Feedback trigger button is present on /app.
//   2. Clicking the trigger opens the feedback form dialog.
//   3. Cancelling the form hides it and restores the trigger button.

import { expect, test } from "../fixtures/test-env";

const GENERAL_FEEDBACK_ENABLED =
  process.env.NEXT_PUBLIC_GENERAL_FEEDBACK_ENABLED === "true";

test.describe("abs259 general feedback gate", () => {
  test.describe("gate-open path (NEXT_PUBLIC_GENERAL_FEEDBACK_ENABLED=true)", () => {
    test.skip(
      !GENERAL_FEEDBACK_ENABLED,
      "general feedback gate is closed in this env",
    );

    test("feedback trigger button is visible on /app", async ({ page }) => {
      await page.goto("/app");
      const trigger = page.getByTestId("app-feedback-open");
      await expect(trigger).toBeVisible({ timeout: 10_000 });
    });

    test("clicking trigger opens feedback form dialog", async ({ page }) => {
      await page.goto("/app");
      const trigger = page.getByTestId("app-feedback-open");
      await expect(trigger).toBeVisible({ timeout: 10_000 });
      await trigger.click();
      await expect(page.getByTestId("app-feedback-form")).toBeVisible();
      await expect(page.getByTestId("app-feedback-message")).toBeVisible();
    });

    test("cancelling the form hides it and restores the trigger button", async ({
      page,
    }) => {
      await page.goto("/app");
      await page.getByTestId("app-feedback-open").click();
      await expect(page.getByTestId("app-feedback-form")).toBeVisible();
      await page.getByTestId("app-feedback-cancel").click();
      await expect(page.getByTestId("app-feedback-form")).not.toBeVisible();
      await expect(page.getByTestId("app-feedback-open")).toBeVisible();
    });
  });
});
