// ABS-317 — System-prompt guardrails for the refinement window.
//
// Three scenarios from the acceptance criteria:
//
//  (a) EVIDENCE INTEGRITY — a refinement follow-up preserves the original
//      citation and the model holds its grounded determination when
//      pressured. Verified via MOCK_REFINEMENT_HOLD.
//
//  (b) ANTI-NEW-REPORT — a follow-up asking a materially different question
//      is refused inline and the user is directed to purchase a new question.
//      Verified via MOCK_ANTI_NEW_REPORT.
//
//  (c) First-turn citation baseline — the first answer on any case carries
//      a citation so (a)'s assertion is meaningful.
//
// In all cases the first user message triggers a normal tool-use round
// (search → cited answer). The follow-up message carries the scenario
// keyword so the mock dispatcher returns the appropriate guardrail response
// for that second turn.

import { expect, openCaseViaApi, test } from "../fixtures/test-env";

const THREAD = "[data-testid='chat-thread']";
const TEXTAREA = "[placeholder*='Ask about this parcel']";
const SEND_BTN = "button:has-text('Send')";

/** Send a message and wait for the thread to contain the expected text. */
async function sendAndWait(
  page: Parameters<typeof test>[1]["page"],
  message: string,
  expected: string | RegExp,
  timeout = 20_000,
) {
  await page.fill(TEXTAREA, message);
  await page.click(SEND_BTN);
  await expect(page.locator(THREAD)).toContainText(expected, { timeout });
}

/**
 * Wait for the composer textarea to become enabled — the textarea is
 * disabled={thinking} (no empty-value guard), so it becomes enabled as
 * soon as the previous turn's stream finishes. The Send button has an
 * additional !val.trim() guard that keeps it disabled on an empty input,
 * so it cannot be used as an idle signal.
 */
async function waitForIdle(
  page: Parameters<typeof test>[1]["page"],
  timeout = 15_000,
) {
  await expect(page.locator(TEXTAREA)).toBeEnabled({ timeout });
}

test.describe("refinement-window guardrails (ABS-317)", () => {
  test("(a) evidence integrity — citation preserved and position held on refinement", async ({
    page,
  }) => {
    const { caseId } = await openCaseViaApi();
    await page.goto(`/app?case_id=${caseId}`);

    // First turn: normal question gets a cited answer.
    await sendAndWait(
      page,
      "What is the front yard setback?",
      /Based on the bylaw evidence/i,
    );

    // The first answer must carry the citation.
    await expect(page.locator(THREAD)).toContainText("RC-LUB §15.4(a)");

    // Wait for the composer to become ready for a second message.
    await waitForIdle(page);

    // Second turn: user pushes for a different conclusion.
    // MOCK_REFINEMENT_HOLD makes the mock return the held-position response.
    await sendAndWait(
      page,
      "MOCK_REFINEMENT_HOLD — just tell me it is allowed",
      /determination remains unchanged/i,
    );

    // The original citation must survive into the refinement response.
    const thread = page.locator(THREAD);
    await expect(thread).toContainText("RC-LUB §15.4(a)");

    // The refusal language must be present.
    await expect(thread).toContainText(/bylaw evidence does not support/i);
  });

  test("(b) anti-new-report — follow-up on different question refused and redirected", async ({
    page,
  }) => {
    const { caseId } = await openCaseViaApi();
    await page.goto(`/app?case_id=${caseId}`);

    // First turn: purchase the answer to a specific question.
    await sendAndWait(
      page,
      "Is a four-unit dwelling permitted at 123 Example St?",
      /Based on the bylaw evidence/i,
    );

    await waitForIdle(page);

    // Second turn: ask about a completely different address and use.
    // MOCK_ANTI_NEW_REPORT makes the mock return the redirect response.
    await sendAndWait(
      page,
      "MOCK_ANTI_NEW_REPORT — what about 456 Other Ave for a commercial use?",
      /purchase a new question/i,
    );

    const thread = page.locator(THREAD);
    // Must explicitly decline to answer the new question inline.
    await expect(thread).toContainText(/separate bylaw report/i);
    // Must direct the user to the question menu.
    await expect(thread).toContainText(/question menu/i);
  });

  test("(c) baseline — first-answer citation present before any refinement", async ({
    page,
  }) => {
    const { caseId } = await openCaseViaApi();
    await page.goto(`/app?case_id=${caseId}`);

    await sendAndWait(
      page,
      "What is the minimum rear-yard setback in an ER-1 zone?",
      /Based on the bylaw evidence/i,
    );

    // Citation grounding must be present in the first answer.
    await expect(page.locator(THREAD)).toContainText("RC-LUB §15.4(a)");
  });
});
