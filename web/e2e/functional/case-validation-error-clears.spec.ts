// Functional: validation errors on /cases/new should clear when the user
// modifies the fields that triggered the error.
//
// Regression for ABS-187: user submits with empty anchor → validation error
// appears. User fills in anchor field, but the error persists (stale state).
// User should see the error clear as soon as they start typing, not wait for
// the next successful classification or case open.

import { expect, test } from "../fixtures/test-env";

test("validation error clears when anchor field is modified", async ({
  page,
}) => {
  await page.goto("/cases/new");

  const messageInput = page.getByPlaceholder(/Describe the inquiry/);
  const anchorInput = page.getByPlaceholder(/1234 Main St, Halifax/);
  const classifyBtn = page.getByRole("button", {
    name: /Get tier recommendation/,
  });

  // Set up: fill message but leave anchor empty
  await messageInput.fill("Test message for classification");

  // Trigger validation error by clicking classify with empty anchor
  await classifyBtn.click();

  // Error appears
  await expect(
    page.getByText(/Anchor and first message are required to classify/i)
  ).toBeVisible({
    timeout: 3000,
  });

  // Now fill anchor field — error should clear immediately
  await anchorInput.fill("1234 Main St, Halifax");

  // Error should disappear as soon as we type in anchor
  await expect(
    page.getByText(/Anchor and first message are required to classify/i)
  ).not.toBeVisible();
});

test("validation error clears when message field is modified", async ({
  page,
}) => {
  await page.goto("/cases/new");

  const messageInput = page.getByPlaceholder(/Describe the inquiry/);
  const anchorInput = page.getByPlaceholder(/1234 Main St, Halifax/);
  const classifyBtn = page.getByRole("button", {
    name: /Get tier recommendation/,
  });

  // Set up: fill anchor but leave message empty
  await anchorInput.fill("1234 Main St, Halifax");
  // Explicitly clear the message field to ensure it's empty
  await messageInput.fill("");

  // Trigger validation error by clicking classify with empty message
  await classifyBtn.click();

  // Error appears
  await expect(
    page.getByText(/Anchor and first message are required to classify/i)
  ).toBeVisible({
    timeout: 5000,
  });

  // Now fill the message field — error should clear immediately
  await messageInput.fill("Test message for classification");

  // Error should disappear as soon as we type in message
  await expect(
    page.getByText(/Anchor and first message are required to classify/i)
  ).not.toBeVisible();
});
