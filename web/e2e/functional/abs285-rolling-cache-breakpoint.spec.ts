// Functional: ABS-285 — rolling intra-loop cache breakpoint.
//
// What this change does (backend cost optimization):
//   1. The tool loop (src/advisor/llm/tool_loop.py) now marks a single
//      rolling Anthropic cache breakpoint on the latest tool_result
//      turn each iteration, so a deep question reads its earlier
//      retrieval rounds from cache instead of re-billing them.
//   2. To stay within Anthropic's 4-breakpoint budget, the chat
//      session (src/advisor/chat/session.py) reallocates one of its two
//      session-start milestone breakpoints to that rolling one — only
//      the FIRST assistant turn is now cached as a milestone.
//
// Why the assertion here is a regression guard, not a cache assertion:
// the e2e stack drives the advisor through MockGateway, which ignores
// cache_control flags entirely (cache reads only happen against the
// real Anthropic backend). The byte-level breakpoint placement is
// therefore verified in the Python unit tests
// (tests/advisor/llm/test_tool_loop.py — rolling breakpoint rides the
// latest tool_result and never leaks into the persisted transcript;
// tests/advisor/chat/test_session.py — exactly one milestone is
// marked). What IS observable end-to-end is that the breakpoint
// reallocation did not break the multi-turn tool loop: each turn still
// runs its search round and streams a correct, complete answer. Against
// real Anthropic an over-allocation would surface as a 5-breakpoint API
// rejection on turn 3+; this guards that the request the session builds
// across several turns stays well-formed and answerable.

import { expect, openCaseViaApi, test } from "../fixtures/test-env";

test("multi-turn tool loop still answers after breakpoint reallocation", async ({
  page,
}) => {
  const { caseId } = await openCaseViaApi();
  await page.goto(`/app?case_id=${caseId}`);

  const textarea = page.getByPlaceholder(/Ask about this parcel/);
  const sendBtn = page.getByRole("button", { name: /^Send/ });
  const thread = page.getByTestId("chat-thread");

  // Three consecutive turns on one case. Turn 3 is the one that, under
  // the OLD allocation, would have stacked two milestone breakpoints +
  // the rolling one; the reallocation keeps it to one milestone so the
  // request stays inside Anthropic's 4-breakpoint budget. Each turn
  // runs a full search → answer tool loop via the mock dispatcher.
  const prompts = [
    "What is the front yard setback?",
    "And the maximum building height?",
    "What about lot coverage limits?",
  ];

  for (const prompt of prompts) {
    await expect(textarea).toBeEnabled();
    await textarea.fill(prompt);
    await sendBtn.click();
    // Each turn must settle with the deterministic mock answer before
    // the next turn is sent — that confirms the tool loop completed and
    // the composer re-enabled.
    await expect(async () => {
      const text = (await thread.innerText()).toLowerCase();
      const occurrences =
        text.split("based on the bylaw evidence").length - 1;
      expect(occurrences).toBeGreaterThanOrEqual(prompts.indexOf(prompt) + 1);
    }).toPass({ timeout: 20_000 });
  }

  // All three turns produced an assistant answer.
  await expect(async () => {
    const text = (await thread.innerText()).toLowerCase();
    const occurrences = text.split("based on the bylaw evidence").length - 1;
    expect(occurrences).toBeGreaterThanOrEqual(3);
  }).toPass({ timeout: 20_000 });
});
