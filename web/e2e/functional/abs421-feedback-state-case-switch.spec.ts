// Functional: per-message feedback state must not leak across sidebar
// case switches.
//
// Regression context (ABS-421): ChatThread keys its messages by array
// index, so a client-side case switch reused the same MessageFeedback
// instance. Its state was seeded from ``savedFeedback`` in useState
// initializers, which only run on mount — so case A's thumbs-down and
// submitted-flag styling rendered on case B's message even though
// advisor_chat_message_feedback had no rows for B. A fresh page load of
// B was clean, which is what made this look like a hydration bug.

import { expect, openCaseViaApi, test } from "../fixtures/test-env";

const ANSWER = /Based on the bylaw evidence/i;

test("switching cases via the sidebar clears the previous case's feedback state", async ({
  page,
}) => {
  const ts = Date.now();
  const firstMessage = "What is the minimum front yard setback?";

  // Case B first, so that after we land on A the sidebar already lists B.
  const anchorB = `9421 Beta Blvd ${ts}, Halifax`;
  const { caseId: caseB } = await openCaseViaApi({ anchorLabel: anchorB });
  await page.goto(
    `/app?case_id=${caseB}&first_message=${encodeURIComponent(firstMessage)}`,
  );
  await expect(page.getByTestId("chat-thread")).toContainText(ANSWER, {
    timeout: 15_000,
  });

  const anchorA = `9421 Alpha Ave ${ts}, Halifax`;
  const { caseId: caseA } = await openCaseViaApi({ anchorLabel: anchorA });
  await page.goto(
    `/app?case_id=${caseA}&first_message=${encodeURIComponent(firstMessage)}`,
  );
  await expect(page.getByTestId("chat-thread")).toContainText(ANSWER, {
    timeout: 15_000,
  });

  // --- Rate case A's answer thumbs-down + submit a flag with reason & notes ---
  await expect(page.getByTestId("message-feedback").first()).toBeVisible({
    timeout: 10_000,
  });

  const thumbsDownPost = page.waitForResponse(
    (r) => r.url().includes("/feedback") && r.status() === 200,
  );
  await page.getByTestId("feedback-thumbs-down").first().click();
  await thumbsDownPost;

  // Thumbs-down auto-opens the flag panel.
  await expect(page.getByTestId("flag-panel")).toBeVisible();
  await page.getByTestId("flag-reason-wrong_zone").click();
  await page.getByTestId("flag-notes").fill("Cited the wrong zone for A.");

  const flagPost = page.waitForResponse(
    (r) => r.url().includes("/feedback") && r.status() === 200,
  );
  await page.getByTestId("flag-submit").click();
  await flagPost;

  await expect(
    page.getByTestId("feedback-thumbs-down").first(),
  ).toHaveAttribute("aria-pressed", "true");
  await expect(page.getByTestId("feedback-flag-btn").first()).toHaveClass(
    /border-accent/,
  );

  // --- Client-side switch to case B via the sidebar ---
  const sidebar = page.locator("aside").first();
  const caseBButton = sidebar.getByRole("button", {
    name: new RegExp(escapeRegExp(`Beta Blvd ${ts}`), "i"),
  });
  await expect(caseBButton).toBeVisible({ timeout: 8_000 });
  await caseBButton.click();

  await expect(page).toHaveURL(new RegExp(`case_id=${caseB}`), {
    timeout: 5_000,
  });
  await expect(page.getByTestId("chat-thread")).toContainText(ANSWER, {
    timeout: 15_000,
  });

  // B has no feedback rows, so its toolbar must render clean.
  const bFeedback = page.getByTestId("message-feedback").first();
  await expect(bFeedback).toBeVisible({ timeout: 10_000 });
  await expect(page.getByTestId("feedback-thumbs-down").first()).toHaveAttribute(
    "aria-pressed",
    "false",
  );
  await expect(page.getByTestId("feedback-thumbs-up").first()).toHaveAttribute(
    "aria-pressed",
    "false",
  );
  await expect(page.getByTestId("feedback-flag-btn").first()).not.toHaveClass(
    /border-accent/,
  );

  // The flag panel must not carry A's reason chip or notes over either.
  await page.getByTestId("feedback-flag-btn").first().click();
  await expect(page.getByTestId("flag-panel")).toBeVisible();
  await expect(page.getByTestId("flag-reason-wrong_zone")).not.toHaveClass(
    /border-accent/,
  );
  await expect(page.getByTestId("flag-notes")).toHaveValue("");
  await expect(page.getByTestId("flag-saved-indicator")).toHaveCount(0);
  await page.getByTestId("flag-cancel").click();

  // --- Switching back to A must re-show A's real, server-persisted state ---
  const caseAButton = sidebar.getByRole("button", {
    name: new RegExp(escapeRegExp(`Alpha Ave ${ts}`), "i"),
  });
  await expect(caseAButton).toBeVisible({ timeout: 8_000 });
  await caseAButton.click();

  await expect(page).toHaveURL(new RegExp(`case_id=${caseA}`), {
    timeout: 5_000,
  });
  await expect(page.getByTestId("message-feedback").first()).toBeVisible({
    timeout: 10_000,
  });
  await expect(page.getByTestId("feedback-thumbs-down").first()).toHaveAttribute(
    "aria-pressed",
    "true",
    { timeout: 10_000 },
  );
  await expect(page.getByTestId("feedback-flag-btn").first()).toHaveClass(
    /border-accent/,
  );

  await page.getByTestId("feedback-flag-btn").first().click();
  await expect(page.getByTestId("flag-reason-wrong_zone")).toHaveClass(
    /border-accent/,
  );
  await expect(page.getByTestId("flag-notes")).toHaveValue(
    "Cited the wrong zone for A.",
  );
});

function escapeRegExp(s: string): string {
  return s.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
}
