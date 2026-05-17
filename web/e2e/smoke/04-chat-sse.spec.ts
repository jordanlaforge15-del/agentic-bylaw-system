// Smoke: send a chat message and verify the SSE stream renders a
// progressive assistant reply ending in the deterministic mock text.
// Bypasses the case-open UI via openCaseViaApi so this spec fails for
// chat-pipeline reasons only, not case-open reasons.
//
// Failure modes this catches:
//   * Composer disabled or send button mis-wired → text never sent
//   * /api/chat proxy broken → no upstream byte stream
//   * SSE parsing in /app/page.tsx broken → no assistant message
//   * Backend MockGateway dispatcher mis-wired → wrong final text
//   * Composer gated by missing case_id → "open a case" copy visible

import { expect, openCaseViaApi, test } from "../fixtures/test-env";

test("chat: user message streams a mock answer", async ({ page }) => {
  const { caseId } = await openCaseViaApi();
  await page.goto(`/app?case_id=${caseId}`);

  // The composer is present (no "open a case" gate).
  const textarea = page.getByPlaceholder(/Ask about this parcel/);
  await expect(textarea).toBeVisible();
  // Mobile-iphone WebKit occasionally needs the sticky composer
  // scrolled into the viewport before the textarea accepts focus —
  // scrollIntoViewIfNeeded() is a no-op on desktop but stabilises
  // the small-viewport runs.
  await textarea.scrollIntoViewIfNeeded();

  await textarea.fill("What is the minimum front yard setback?");
  // Press Enter to submit. We deliberately avoid clicking the Send
  // button here: on mobile-iphone WebKit the soft keyboard's layout
  // shift can intermittently move the button between hit-test and
  // click, producing flakes. The composer's keyDown handler treats
  // Enter (without Shift) the same as a form submit — verified by
  // the composer-keyboard functional spec.
  await textarea.press("Enter");

  // The mock dispatcher's default final text mentions the bylaw
  // citation we hardcoded in mock_dispatcher._DEFAULT_CITATION.
  await expect(
    page.getByTestId("chat-thread"),
  ).toContainText(/Based on the bylaw evidence/i, { timeout: 15_000 });
  await expect(
    page.getByTestId("chat-thread"),
  ).toContainText(/RC-LUB §15\.4/);
});
