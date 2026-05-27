// Functional: validation errors on /cases/new should clear when the user
// modifies the fields that triggered the error.
//
// Regression for ABS-187: user submits with empty anchor → validation error
// appears. User fills in anchor field, but the error persists (stale state).
// User should see the error clear as soon as they start typing, not wait for
// the next successful classification or case open.
//
// This spec tests that:
// 1. Submitting with empty anchor shows a validation error
// 2. Typing in the anchor field clears the error
// 3. Submitting with empty message shows a validation error
// 4. Typing in the message field clears the error

import { expect, test } from "../fixtures/test-env";

test("validation error clears when anchor field is modified", async ({
  page,
}) => {
  await page.goto("/cases/new");

  const messageInput = page.getByPlaceholder(/Describe the inquiry/);
  await messageInput.fill("Test message");

  const classifyBtn = page.getByRole("button", {
    name: /Get tier recommendation/,
  });

  // Submit with empty anchor — should show validation error
  await classifyBtn.click();
  await expect(
    page.getByText(/Anchor and first message are required to classify/i)
  ).toBeVisible();

  // Type in the anchor field — error should clear
  const anchorInput = page.getByPlaceholder(/1234 Main St, Halifax/);
  await anchorInput.fill("1234 Main St, Halifax");

  // Error should be gone
  await expect(
    page.getByText(/Anchor and first message are required to classify/i)
  ).not.toBeVisible();
});

test("validation error clears when message field is modified", async ({
  page,
}) => {
  await page.goto("/cases/new");

  const anchorInput = page.getByPlaceholder(/1234 Main St, Halifax/);
  await anchorInput.fill("1234 Main St, Halifax");

  const classifyBtn = page.getByRole("button", {
    name: /Get tier recommendation/,
  });

  // Submit with empty message — should show validation error
  await classifyBtn.click();
  await expect(
    page.getByText(/Anchor and first message are required to classify/i)
  ).toBeVisible();

  // Type in the message field — error should clear
  const messageInput = page.getByPlaceholder(/Describe the inquiry/);
  await messageInput.fill("Test message");

  // Error should be gone
  await expect(
    page.getByText(/Anchor and first message are required to classify/i)
  ).not.toBeVisible();
});

test("successful classification clears any previous validation error", async ({
  page,
}) => {
  await page.goto("/cases/new");

  const messageInput = page.getByPlaceholder(/Describe the inquiry/);
  const anchorInput = page.getByPlaceholder(/1234 Main St, Halifax/);
  const classifyBtn = page.getByRole("button", {
    name: /Get tier recommendation/,
  });

  // Submit with empty anchor — shows validation error
  await messageInput.fill("Test message");
  await classifyBtn.click();
  await expect(
    page.getByText(/Anchor and first message are required to classify/i)
  ).toBeVisible();

  // Fill anchor and submit again
  await anchorInput.fill(`test-${Date.now()}`);
  await classifyBtn.click();

  // Validation error should be gone and classifier should succeed
  await expect(
    page.getByText(/Anchor and first message are required to classify/i)
  ).not.toBeVisible();

  // Classifier result should be visible (confidence percentage)
  await expect(page.getByText(/CONFIDENCE/i)).toBeVisible({
    timeout: 5_000,
  });
});
