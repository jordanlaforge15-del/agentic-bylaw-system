// Functional: question and response survive navigating away and back.
//
// Regression context (ABS-218): submitting a question in Case A then
// immediately clicking a different case in the sidebar caused the stream
// to be aborted. On returning to Case A the message list was re-fetched
// from the server which had not yet saved the in-progress response, so
// the user's question (and any partial reply) disappeared entirely.
//
// Fix: selectSession() now snapshots the current case's messages before
// switching away, and restores from that snapshot when navigating back if
// the snapshot is more complete than the server response.
//
// Two test paths:
//  1. Navigate-away-during-stream: submit a question, switch to another
//     case BEFORE the response arrives, navigate back — user's question
//     must still be visible.
//  2. Navigate-away-after-stream: submit, wait for full response, switch,
//     come back — both question and response must be visible (baseline,
//     verifies no regression in the common path).

import {
  expect,
  expectCaseIdInUrl,
  openCaseViaApi,
  test,
  waitForHydration,
  waitForSessionListed,
} from "../fixtures/test-env";

// Helper: fill the composer and press Enter to submit.
async function submitQuestion(page: import("@playwright/test").Page, text: string) {
  const textarea = page.getByPlaceholder(/Ask about this parcel/);
  await expect(textarea).toBeVisible();
  await textarea.scrollIntoViewIfNeeded();
  await waitForHydration(page, 'textarea[placeholder^="Ask about this parcel"]');
  await textarea.fill(text);
  await textarea.press("Enter");
}

// Helper: click a session in the sidebar whose title contains the given text.
async function clickSidebarSession(
  page: import("@playwright/test").Page,
  anchorFragment: string,
) {
  const sidebar = page.locator("aside").first();
  const btn = sidebar.getByRole("button", {
    name: new RegExp(anchorFragment.replace(/[.*+?^${}()|[\]\\]/g, "\\$&"), "i"),
  });
  await expect(btn).toBeVisible({ timeout: 8_000 });
  await btn.click();
}

test(
  "user question persists when navigating away before response arrives",
  async ({ page }) => {
    const ts = Date.now();
    const question = "What is the minimum front yard setback?";

    const anchorA = `9101 Alpha St ${ts}`;
    const anchorB = `9102 Beta St ${ts}`;
    const { caseId: caseA } = await openCaseViaApi({ anchorLabel: anchorA });
    const { caseId: caseB } = await openCaseViaApi({ anchorLabel: anchorB });

    // Seed case B with a completed turn so it appears in the sidebar.
    await page.goto(
      `/app?case_id=${caseB}&first_message=${encodeURIComponent(question)}`,
    );
    await expect(page.getByTestId("chat-thread")).toContainText(
      /Based on the bylaw evidence/i,
      { timeout: 15_000 },
    );

    // Land on case A.
    await page.goto(`/app?case_id=${caseA}`);
    await expect(page).toHaveURL(new RegExp(`case_id=${caseA}`));

    // Submit a question in case A and immediately switch to case B —
    // before the "reading…" indicator has a chance to disappear.
    await submitQuestion(page, question);

    // Confirm the thinking indicator appears (we're mid-stream).
    await expect(page.getByTestId("chat-thread")).toContainText(
      /ABS · READING/i,
      { timeout: 5_000 },
    );

    // Case A's session row is committed when the turn opens — but the sidebar
    // only refetches once, in the aborted turn's finally, so the switch below
    // races that commit. Wait for the server to list A first; we are still
    // mid-stream (the answer has not arrived) but the refetch can no longer
    // miss A and strand us with nothing to click back to (ABS-460).
    await waitForSessionListed(page, `Alpha St ${ts}`);

    // Switch to case B while the stream is still in progress.
    await clickSidebarSession(page, `Beta St ${ts}`);
    await expectCaseIdInUrl(page, caseB);

    // Navigate back to case A by clicking its sidebar entry.
    await clickSidebarSession(page, `Alpha St ${ts}`);
    await expectCaseIdInUrl(page, caseA);

    // The user's question must be visible — it should never disappear.
    await expect(page.getByTestId("chat-thread")).toContainText(
      question,
      { timeout: 5_000 },
    );
  },
);

test(
  "completed response survives navigating away and back",
  async ({ page }) => {
    const ts = Date.now();
    const question = "What is the minimum front yard setback?";

    const anchorA = `9201 Alpha Ave ${ts}`;
    const anchorB = `9202 Beta Ave ${ts}`;
    const { caseId: caseA } = await openCaseViaApi({ anchorLabel: anchorA });
    const { caseId: caseB } = await openCaseViaApi({ anchorLabel: anchorB });

    // Seed case B so it appears in the sidebar.
    await page.goto(
      `/app?case_id=${caseB}&first_message=${encodeURIComponent(question)}`,
    );
    await expect(page.getByTestId("chat-thread")).toContainText(
      /Based on the bylaw evidence/i,
      { timeout: 15_000 },
    );

    // Land on case A and wait for the full response.
    await page.goto(
      `/app?case_id=${caseA}&first_message=${encodeURIComponent(question)}`,
    );
    await expect(page.getByTestId("chat-thread")).toContainText(
      /Based on the bylaw evidence/i,
      { timeout: 15_000 },
    );

    // Switch to case B.
    await clickSidebarSession(page, `Beta Ave ${ts}`);
    await expectCaseIdInUrl(page, caseB);

    // Navigate back to case A.
    await clickSidebarSession(page, `Alpha Ave ${ts}`);
    await expectCaseIdInUrl(page, caseA);

    // Both the question and the response must be restored.
    const thread = page.getByTestId("chat-thread");
    await expect(thread).toContainText(question, { timeout: 8_000 });
    await expect(thread).toContainText(/Based on the bylaw evidence/i, {
      timeout: 8_000,
    });
  },
);
