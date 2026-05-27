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
