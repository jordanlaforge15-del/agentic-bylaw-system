// ABS-313 — Per-answer disclaimer on paid outputs.
//
// Asserts:
//  1. Every agent reply carries the on-output disclaimer required for
//     day-one liability posture: "Bylaw-analysis output — not an
//     official municipal determination. Verify before relying."
//  2. The disclaimer is present after the first reply streams in.
//  3. Multiple replies each carry their own disclaimer instance.
//
// Why all agent replies, not just "paid" ones: the AgentMessage type
// does not carry a billing flag in this revision. Every answer on a
// case uses turn credits, so all answers are priced answers.

import { expect, openCaseViaApi, test } from "../fixtures/test-env";

test.describe("paid-answer disclaimer (ABS-313)", () => {
  test("disclaimer appears on the first agent reply", async ({ page }) => {
    const { caseId } = await openCaseViaApi();
    await page.goto(`/app?case_id=${caseId}`);

    const textarea = page.getByPlaceholder(/Ask about this parcel/);
    const sendBtn = page.getByRole("button", { name: /^Send/ });
    const thread = page.getByTestId("chat-thread");

    await textarea.fill("What is the maximum lot coverage?");
    await sendBtn.click();

    // Wait for the agent reply to stream in.
    await expect(thread).toContainText(/Based on the bylaw evidence/i, {
      timeout: 15_000,
    });

    // The disclaimer must be visible in the thread.
    const disclaimer = thread.getByTestId("paid-answer-disclaimer").first();
    await expect(disclaimer).toBeVisible();
    await expect(disclaimer).toContainText(
      /not an official municipal determination/i,
    );
    await expect(disclaimer).toContainText(/verify before relying/i);
  });

  test("each agent reply has its own disclaimer", async ({ page }) => {
    const { caseId } = await openCaseViaApi();
    await page.goto(`/app?case_id=${caseId}`);

    const textarea = page.getByPlaceholder(/Ask about this parcel/);
    const sendBtn = page.getByRole("button", { name: /^Send/ });
    const thread = page.getByTestId("chat-thread");

    await textarea.fill("What is the maximum building height?");
    await sendBtn.click();
    await expect(thread).toContainText(/Based on the bylaw evidence/i, {
      timeout: 15_000,
    });

    await expect(textarea).toBeEnabled();
    await textarea.fill("Is a secondary suite permitted here?");
    await sendBtn.click();

    // Wait for the second reply.
    await expect(async () => {
      const text = (await thread.innerText()).toLowerCase();
      const hits = text.split("based on the bylaw evidence").length - 1;
      expect(hits).toBeGreaterThanOrEqual(2);
    }).toPass({ timeout: 20_000 });

    // Both replies should each have a disclaimer.
    const disclaimers = thread.getByTestId("paid-answer-disclaimer");
    await expect(disclaimers).toHaveCount(2, { timeout: 5_000 });
  });
});
