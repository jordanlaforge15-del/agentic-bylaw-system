// Functional: general feedback form — accessible from the app shell sidebar.
//
// Verifies that the "Give feedback" button appears in the sidebar, that the
// form opens with category chips and a message textarea, that submit is
// disabled until both category and message are provided, and that a successful
// submission shows a confirmation message.

import { expect, test } from "../fixtures/test-env";

test("general feedback: button visible in sidebar", async ({ page }) => {
  await page.goto("/app");

  const feedbackBtn = page.getByTestId("general-feedback-open");
  await expect(feedbackBtn).toBeVisible();
});

test("general feedback: form opens with category chips and textarea", async ({
  page,
}) => {
  await page.goto("/app");

  const feedbackBtn = page.getByTestId("general-feedback-open");
  await feedbackBtn.click();

  const form = page.getByTestId("general-feedback-form");
  await expect(form).toBeVisible();

  // All four category chips should be visible
  await expect(page.getByTestId("feedback-category-ux_issue")).toBeVisible();
  await expect(
    page.getByTestId("feedback-category-feature_request"),
  ).toBeVisible();
  await expect(
    page.getByTestId("feedback-category-general_satisfaction"),
  ).toBeVisible();
  await expect(page.getByTestId("feedback-category-other")).toBeVisible();

  // Textarea visible
  await expect(page.getByTestId("general-feedback-message")).toBeVisible();
});

test("general feedback: submit disabled until category and message are filled", async ({
  page,
}) => {
  await page.goto("/app");

  await page.getByTestId("general-feedback-open").click();

  const submitBtn = page.getByTestId("general-feedback-submit");

  // Initially disabled (no category, no message)
  await expect(submitBtn).toBeDisabled();

  // Category selected but no message — still disabled
  await page.getByTestId("feedback-category-ux_issue").click();
  await expect(submitBtn).toBeDisabled();

  // Both category and message — enabled
  await page.getByTestId("general-feedback-message").fill("The zone picker is confusing.");
  await expect(submitBtn).toBeEnabled();
});

test("general feedback: cancel closes the form", async ({ page }) => {
  await page.goto("/app");

  await page.getByTestId("general-feedback-open").click();
  await expect(page.getByTestId("general-feedback-form")).toBeVisible();

  await page.getByTestId("general-feedback-cancel").click();
  await expect(page.getByTestId("general-feedback-form")).not.toBeVisible();
  // The open button should be visible again
  await expect(page.getByTestId("general-feedback-open")).toBeVisible();
});

test("general feedback: submit sends feedback and shows confirmation", async ({
  page,
}) => {
  await page.goto("/app");

  await page.getByTestId("general-feedback-open").click();

  await page.getByTestId("feedback-category-feature_request").click();
  await page
    .getByTestId("general-feedback-message")
    .fill("Please add a dark mode toggle to the header.");

  await page.getByTestId("general-feedback-submit").click();

  // Form closes and thank-you message appears
  await expect(page.getByTestId("general-feedback-form")).not.toBeVisible();
  await expect(page.getByTestId("general-feedback-thanks")).toBeVisible();
});

test("general feedback: acknowledgment auto-dismisses after 4 seconds", async ({
  page,
}) => {
  await page.goto("/app");

  await page.getByTestId("general-feedback-open").click();
  await page.getByTestId("feedback-category-ux_issue").click();
  await page
    .getByTestId("general-feedback-message")
    .fill("The sidebar is hard to find.");
  await page.getByTestId("general-feedback-submit").click();

  // Confirmation message visible
  await expect(page.getByTestId("general-feedback-thanks")).toBeVisible();
  // Form is not visible
  await expect(page.getByTestId("general-feedback-form")).not.toBeVisible();

  // Wait for auto-dismiss (4 seconds)
  await page.waitForTimeout(4500);

  // After dismissal, thanks message is gone and button reappears
  await expect(page.getByTestId("general-feedback-thanks")).not.toBeVisible();
  await expect(page.getByTestId("general-feedback-open")).toBeVisible();
});

test("general feedback: can submit again after acknowledgment auto-dismisses", async ({
  page,
}) => {
  await page.goto("/app");

  // First submission
  await page.getByTestId("general-feedback-open").click();
  await page.getByTestId("feedback-category-feature_request").click();
  await page
    .getByTestId("general-feedback-message")
    .fill("First feedback message");
  await page.getByTestId("general-feedback-submit").click();

  // Wait for acknowledgment to auto-dismiss
  await page.waitForTimeout(4500);

  // Button should be visible again
  await expect(page.getByTestId("general-feedback-open")).toBeVisible();

  // Second submission
  await page.getByTestId("general-feedback-open").click();
  await page.getByTestId("feedback-category-ux_issue").click();
  await page
    .getByTestId("general-feedback-message")
    .fill("Second feedback message");
  await page.getByTestId("general-feedback-submit").click();

  // Confirmation message for second submission visible
  await expect(page.getByTestId("general-feedback-thanks")).toBeVisible();
});
