// Functional: chat message feedback UI — thumbs up/down and flag/report.
//
// Verifies that agent messages render the feedback toolbar, that thumbs
// buttons toggle visually and POST to the backend, and that the flag
// panel opens and submits a report.

import { expect, openCaseViaApi, test } from "../fixtures/test-env";

test("feedback: thumbs up/down buttons appear and toggle on agent messages", async ({
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

  // After stream finishes + refreshFromSession, feedback buttons render.
  const feedbackContainer = page.getByTestId("message-feedback").first();
  await expect(feedbackContainer).toBeVisible({ timeout: 10_000 });

  // Thumbs up
  const thumbsUp = page.getByTestId("feedback-thumbs-up").first();
  await expect(thumbsUp).toBeVisible();
  await thumbsUp.click();
  await expect(thumbsUp).toHaveAttribute("aria-pressed", "true");

  // Click again to un-toggle
  await thumbsUp.click();
  await expect(thumbsUp).toHaveAttribute("aria-pressed", "false");

  // Thumbs down
  const thumbsDown = page.getByTestId("feedback-thumbs-down").first();
  await thumbsDown.click();
  await expect(thumbsDown).toHaveAttribute("aria-pressed", "true");
});

test("feedback: re-clicking selected thumbs button clears rating on server", async ({
  page,
}) => {
  const { caseId } = await openCaseViaApi();
  await page.goto(`/app?case_id=${caseId}`);

  const textarea = page.getByPlaceholder(/Ask about this parcel/);
  const sendBtn = page.getByRole("button", { name: /^Send/ });
  const thread = page.getByTestId("chat-thread");

  await textarea.fill("What are the setback requirements?");
  await sendBtn.click();
  await expect(thread).toContainText(/Based on the bylaw evidence/i, {
    timeout: 15_000,
  });

  const feedbackContainer = page.getByTestId("message-feedback").first();
  await expect(feedbackContainer).toBeVisible({ timeout: 10_000 });

  const thumbsUp = page.getByTestId("feedback-thumbs-up").first();

  // Intercept first click to verify rating: "up"
  const promise1 = page.waitForResponse(
    (response) =>
      response.url().includes("/feedback") && response.status() === 200,
  );
  await thumbsUp.click();
  const response1 = await promise1;
  const body1 = JSON.parse(
    response1.request().postDataBuffer()?.toString() || "{}",
  );
  expect(body1.rating).toBe("up");
  await expect(thumbsUp).toHaveAttribute("aria-pressed", "true");

  // Intercept second click to verify rating: null
  const promise2 = page.waitForResponse(
    (response) =>
      response.url().includes("/feedback") && response.status() === 200,
  );
  await thumbsUp.click();
  const response2 = await promise2;
  const body2 = JSON.parse(
    response2.request().postDataBuffer()?.toString() || "{}",
  );
  expect(body2.rating).toBe(null);
  await expect(thumbsUp).toHaveAttribute("aria-pressed", "false");
});

test("feedback: flag panel opens, requires reason, and submits", async ({
  page,
}) => {
  const { caseId } = await openCaseViaApi();
  await page.goto(`/app?case_id=${caseId}`);

  const textarea = page.getByPlaceholder(/Ask about this parcel/);
  const sendBtn = page.getByRole("button", { name: /^Send/ });
  const thread = page.getByTestId("chat-thread");

  await textarea.fill("Can I build a 10-storey tower here?");
  await sendBtn.click();
  await expect(thread).toContainText(/Based on the bylaw evidence/i, {
    timeout: 15_000,
  });

  const feedbackContainer = page.getByTestId("message-feedback").first();
  await expect(feedbackContainer).toBeVisible({ timeout: 10_000 });

  // Open flag panel
  const flagBtn = page.getByTestId("feedback-flag-btn").first();
  await flagBtn.click();
  const flagPanel = page.getByTestId("flag-panel");
  await expect(flagPanel).toBeVisible();

  // Submit disabled without reason
  const submitBtn = page.getByTestId("flag-submit");
  await expect(submitBtn).toBeDisabled();

  // Select a reason
  await page.getByTestId("flag-reason-hallucination").click();
  await expect(submitBtn).toBeEnabled();

  // Add optional notes
  const notes = page.getByTestId("flag-notes");
  await notes.fill("The cited section does not exist.");

  // Submit
  await submitBtn.click();
  await expect(flagPanel).not.toBeVisible();
});

test("feedback: cancel closes flag panel without submitting", async ({
  page,
}) => {
  const { caseId } = await openCaseViaApi();
  await page.goto(`/app?case_id=${caseId}`);

  const textarea = page.getByPlaceholder(/Ask about this parcel/);
  const sendBtn = page.getByRole("button", { name: /^Send/ });
  const thread = page.getByTestId("chat-thread");

  await textarea.fill("What is the minimum lot size?");
  await sendBtn.click();
  await expect(thread).toContainText(/Based on the bylaw evidence/i, {
    timeout: 15_000,
  });

  const feedbackContainer = page.getByTestId("message-feedback").first();
  await expect(feedbackContainer).toBeVisible({ timeout: 10_000 });

  const flagBtn = page.getByTestId("feedback-flag-btn").first();
  await flagBtn.click();
  await expect(page.getByTestId("flag-panel")).toBeVisible();

  await page.getByTestId("flag-cancel").click();
  await expect(page.getByTestId("flag-panel")).not.toBeVisible();
});

test("feedback: thumbs shows toast and auto-dismisses", async ({ page }) => {
  const { caseId } = await openCaseViaApi();
  await page.goto(`/app?case_id=${caseId}`);

  const textarea = page.getByPlaceholder(/Ask about this parcel/);
  const sendBtn = page.getByRole("button", { name: /^Send/ });
  const thread = page.getByTestId("chat-thread");

  await textarea.fill("What is the setback distance?");
  await sendBtn.click();
  await expect(thread).toContainText(/Based on the bylaw evidence/i, {
    timeout: 15_000,
  });

  const feedbackContainer = page.getByTestId("message-feedback").first();
  await expect(feedbackContainer).toBeVisible({ timeout: 10_000 });

  const thumbsUp = page.getByTestId("feedback-thumbs-up").first();
  await thumbsUp.click();

  // Toast should appear
  await expect(page.getByTestId("feedback-toast-thumbs").first()).toBeVisible();
  await expect(page.getByTestId("feedback-toast-thumbs").first()).toContainText(
    "Thanks — feedback recorded."
  );

  // Toast should auto-dismiss after ~2.5s
  await expect(page.getByTestId("feedback-toast-thumbs").first()).not.toBeVisible({
    timeout: 3000,
  });
});

test("thumbs-down: auto-opens flag panel with 'What went wrong?' header", async ({
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

  // Clicking thumbs-down should auto-open the flag panel
  const thumbsDown = page.getByTestId("feedback-thumbs-down").first();
  await thumbsDown.click();
  await expect(thumbsDown).toHaveAttribute("aria-pressed", "true");

  const flagPanel = page.getByTestId("flag-panel");
  await expect(flagPanel).toBeVisible();

  // Panel should show "What went wrong?" header, not "Report an issue"
  await expect(flagPanel).toContainText("What went wrong?");
  await expect(flagPanel).not.toContainText("Report an issue");
});

test("thumbs-down panel: skip closes panel and rating stays down", async ({
  page,
}) => {
  const { caseId } = await openCaseViaApi();
  await page.goto(`/app?case_id=${caseId}`);

  const textarea = page.getByPlaceholder(/Ask about this parcel/);
  const sendBtn = page.getByRole("button", { name: /^Send/ });
  const thread = page.getByTestId("chat-thread");

  await textarea.fill("What are the setback requirements?");
  await sendBtn.click();
  await expect(thread).toContainText(/Based on the bylaw evidence/i, {
    timeout: 15_000,
  });

  const feedbackContainer = page.getByTestId("message-feedback").first();
  await expect(feedbackContainer).toBeVisible({ timeout: 10_000 });

  const thumbsDown = page.getByTestId("feedback-thumbs-down").first();
  await thumbsDown.click();
  await expect(page.getByTestId("flag-panel")).toBeVisible();

  // Skip button should be present (not "Cancel")
  const skipBtn = page.getByTestId("flag-cancel").first();
  await expect(skipBtn).toContainText("Skip");

  await skipBtn.click();

  // Panel closes, rating stays "down"
  await expect(page.getByTestId("flag-panel")).not.toBeVisible();
  await expect(thumbsDown).toHaveAttribute("aria-pressed", "true");
});

test("thumbs-down panel: submit is enabled without selecting a reason", async ({
  page,
}) => {
  const { caseId } = await openCaseViaApi();
  await page.goto(`/app?case_id=${caseId}`);

  const textarea = page.getByPlaceholder(/Ask about this parcel/);
  const sendBtn = page.getByRole("button", { name: /^Send/ });
  const thread = page.getByTestId("chat-thread");

  await textarea.fill("Can I build a deck here?");
  await sendBtn.click();
  await expect(thread).toContainText(/Based on the bylaw evidence/i, {
    timeout: 15_000,
  });

  const feedbackContainer = page.getByTestId("message-feedback").first();
  await expect(feedbackContainer).toBeVisible({ timeout: 10_000 });

  await page.getByTestId("feedback-thumbs-down").first().click();
  await expect(page.getByTestId("flag-panel")).toBeVisible();

  // Submit should be enabled even without selecting a reason chip
  const submitBtn = page.getByTestId("flag-submit");
  await expect(submitBtn).toBeEnabled();

  // Add notes and submit — panel should close
  await page.getByTestId("flag-notes").fill("The answer seemed incorrect.");
  await submitBtn.click();
  await expect(page.getByTestId("flag-panel")).not.toBeVisible();
});

test("thumbs-down panel: submitting sends flag data with down rating", async ({
  page,
}) => {
  const { caseId } = await openCaseViaApi();
  await page.goto(`/app?case_id=${caseId}`);

  const textarea = page.getByPlaceholder(/Ask about this parcel/);
  const sendBtn = page.getByRole("button", { name: /^Send/ });
  const thread = page.getByTestId("chat-thread");

  await textarea.fill("What is the floor space ratio?");
  await sendBtn.click();
  await expect(thread).toContainText(/Based on the bylaw evidence/i, {
    timeout: 15_000,
  });

  const feedbackContainer = page.getByTestId("message-feedback").first();
  await expect(feedbackContainer).toBeVisible({ timeout: 10_000 });

  await page.getByTestId("feedback-thumbs-down").first().click();
  await expect(page.getByTestId("flag-panel")).toBeVisible();

  // Select a reason chip
  await page.getByTestId("flag-reason-wrong_zone").click();

  // Intercept the flag submit request
  const flagPromise = page.waitForResponse(
    (response) =>
      response.url().includes("/feedback") && response.status() === 200,
  );

  await page.getByTestId("flag-submit").click();

  const flagResponse = await flagPromise;
  const body = JSON.parse(
    flagResponse.request().postDataBuffer()?.toString() || "{}",
  );
  expect(body.rating).toBe("down");
  expect(body.flag_reason).toBe("wrong_zone");
});

test("feedback: flag submission shows toast and shows 'Saved.' on reopen", async ({
  page,
}) => {
  const { caseId } = await openCaseViaApi();
  await page.goto(`/app?case_id=${caseId}`);

  const textarea = page.getByPlaceholder(/Ask about this parcel/);
  const sendBtn = page.getByRole("button", { name: /^Send/ });
  const thread = page.getByTestId("chat-thread");

  await textarea.fill("What are the height limits?");
  await sendBtn.click();
  await expect(thread).toContainText(/Based on the bylaw evidence/i, {
    timeout: 15_000,
  });

  const feedbackContainer = page.getByTestId("message-feedback").first();
  await expect(feedbackContainer).toBeVisible({ timeout: 10_000 });

  // Open flag panel and submit
  const flagBtn = page.getByTestId("feedback-flag-btn").first();
  await flagBtn.click();
  const flagPanel = page.getByTestId("flag-panel");
  await expect(flagPanel).toBeVisible();

  await page.getByTestId("flag-reason-hallucination").click();
  const submitBtn = page.getByTestId("flag-submit");
  await submitBtn.click();

  // Toast should appear
  await expect(page.getByTestId("feedback-toast-flag").first()).toBeVisible();
  await expect(page.getByTestId("feedback-toast-flag").first()).toContainText(
    "Thanks — feedback recorded."
  );

  // Toast should auto-dismiss
  await expect(page.getByTestId("feedback-toast-flag").first()).not.toBeVisible({
    timeout: 3000,
  });

  // Reopen flag panel and check for "Saved." indicator
  await flagBtn.click();
  await expect(page.getByTestId("flag-panel")).toBeVisible();
  await expect(page.getByTestId("flag-saved-indicator")).toBeVisible();
  await expect(page.getByTestId("flag-saved-indicator")).toContainText("Saved.");
});
