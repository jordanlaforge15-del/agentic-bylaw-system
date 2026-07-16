// Functional: feedback state hydrates from server on session load.
//
// Verifies that after submitting per-message feedback and reloading the
// page, thumbs/flag button state and the flag panel contents are
// correctly restored from the GET /api/chat/sessions/{id}/feedback
// endpoint.

import { expect, openCaseViaApi, test } from "../fixtures/test-env";

test("feedback state hydrates on page reload: thumbs-down + flag", async ({
  page,
}) => {
  const { caseId } = await openCaseViaApi();
  await page.goto(`/app?case_id=${caseId}`);

  const textarea = page.getByPlaceholder(/Ask about this parcel/);
  const sendBtn = page.getByRole("button", { name: /^Send/ });
  const thread = page.getByTestId("chat-thread");

  await textarea.fill("What zone is this property in?");
  await sendBtn.click();
  await expect(thread).toContainText(/Based on the bylaw evidence/i, {
    timeout: 15_000,
  });

  const feedbackContainer = page.getByTestId("message-feedback").first();
  await expect(feedbackContainer).toBeVisible({ timeout: 10_000 });

  // Submit thumbs-down and wait for the rating POST to succeed
  const thumbsDownPromise = page.waitForResponse(
    (r) => r.url().includes("/feedback") && r.status() === 200,
  );
  await page.getByTestId("feedback-thumbs-down").first().click();
  await thumbsDownPromise;

  // Flag panel auto-opens on thumbs-down
  await expect(page.getByTestId("flag-panel")).toBeVisible();

  // Select reason and add notes
  await page.getByTestId("flag-reason-wrong_zone").click();
  await page.getByTestId("flag-notes").fill("The cited zone is incorrect.");

  // Submit flag and wait for the POST
  const flagPromise = page.waitForResponse(
    (r) => r.url().includes("/feedback") && r.status() === 200,
  );
  await page.getByTestId("flag-submit").click();
  await flagPromise;

  // Verify panel closed and flag toast appeared
  await expect(page.getByTestId("flag-panel")).not.toBeVisible();
  await expect(page.getByTestId("feedback-toast-flag").first()).toBeVisible();

  // --- Reload the page ---
  await page.reload();

  // Wait for session to be restored (agent message re-appears)
  await expect(thread).toContainText(/Based on the bylaw evidence/i, {
    timeout: 15_000,
  });

  // Wait for feedback toolbar to render
  const feedbackAfterReload = page.getByTestId("message-feedback").first();
  await expect(feedbackAfterReload).toBeVisible({ timeout: 10_000 });

  // Thumbs-down should be pressed (hydrated from server)
  const thumbsDown = page.getByTestId("feedback-thumbs-down").first();
  await expect(thumbsDown).toHaveAttribute("aria-pressed", "true");

  // Thumbs-up should NOT be pressed
  const thumbsUp = page.getByTestId("feedback-thumbs-up").first();
  await expect(thumbsUp).toHaveAttribute("aria-pressed", "false");

  // Flag button should show submitted state (has accent border)
  const flagBtn = page.getByTestId("feedback-flag-btn").first();
  await expect(flagBtn).toHaveClass(/border-accent/);

  // Open flag panel and verify pre-populated state
  await flagBtn.click();
  const flagPanel = page.getByTestId("flag-panel");
  await expect(flagPanel).toBeVisible();

  // Reason chip "wrong_zone" should be selected (has accent styling)
  const wrongZoneChip = page.getByTestId("flag-reason-wrong_zone");
  await expect(wrongZoneChip).toHaveClass(/border-accent/);

  // Notes textarea should contain the previously saved text
  const notes = page.getByTestId("flag-notes");
  await expect(notes).toHaveValue("The cited zone is incorrect.");

  // "Saved." indicator should be visible
  await expect(page.getByTestId("flag-saved-indicator")).toBeVisible();
});

test("feedback state hydrates on page reload: thumbs-up only", async ({
  page,
}) => {
  const { caseId } = await openCaseViaApi();
  await page.goto(`/app?case_id=${caseId}`);

  const textarea = page.getByPlaceholder(/Ask about this parcel/);
  const sendBtn = page.getByRole("button", { name: /^Send/ });
  const thread = page.getByTestId("chat-thread");

  await textarea.fill("What is the maximum height allowed?");
  await sendBtn.click();
  await expect(thread).toContainText(/Based on the bylaw evidence/i, {
    timeout: 15_000,
  });

  const feedbackContainer = page.getByTestId("message-feedback").first();
  await expect(feedbackContainer).toBeVisible({ timeout: 10_000 });

  // Submit thumbs-up
  const thumbsUpPromise = page.waitForResponse(
    (r) => r.url().includes("/feedback") && r.status() === 200,
  );
  await page.getByTestId("feedback-thumbs-up").first().click();
  await thumbsUpPromise;

  // --- Reload the page ---
  await page.reload();

  await expect(thread).toContainText(/Based on the bylaw evidence/i, {
    timeout: 15_000,
  });

  const feedbackAfterReload = page.getByTestId("message-feedback").first();
  await expect(feedbackAfterReload).toBeVisible({ timeout: 10_000 });

  // Thumbs-up should be pressed after reload
  const thumbsUp = page.getByTestId("feedback-thumbs-up").first();
  await expect(thumbsUp).toHaveAttribute("aria-pressed", "true");

  // Thumbs-down should NOT be pressed
  const thumbsDown = page.getByTestId("feedback-thumbs-down").first();
  await expect(thumbsDown).toHaveAttribute("aria-pressed", "false");

  // Flag button should NOT show submitted state
  const flagBtn = page.getByTestId("feedback-flag-btn").first();
  await expect(flagBtn).not.toHaveClass(/border-accent/);
});
