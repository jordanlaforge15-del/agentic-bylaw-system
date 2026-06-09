// Functional: ABS-289 (WI-3) — persona fan-out instruction.
//
// What this change does:
//   persona.md now contains a "Fan out independent lookups in one turn"
//   section instructing the model to issue multiple search_bylaw_evidence /
//   lookup_citation calls in the SAME response when the queries are
//   independent — so all results arrive in one tool_result turn instead of
//   N serial rounds.
//
// Why this is a regression guard, not a parallelism assertion:
//   The e2e stack drives the advisor through MockGateway with a
//   deterministic MOCK_FAN_OUT keyword. The mock emits TWO
//   search_bylaw_evidence tool_use blocks in a single assistant response;
//   the tool loop (tool_loop.py:273) executes both via asyncio.gather,
//   collects both tool_results, appends them as a single user turn, and
//   calls the gateway again for synthesis. The byte-level parallelism is
//   verified in tests/advisor/llm/test_tool_loop.py
//   (test_multiple_tool_calls_in_one_response_handled_in_order).
//
//   What IS observable end-to-end is the load-bearing safety property:
//   when the mock fans two calls into one response, the tool loop must
//   still complete and stream a correct answer — i.e. two tool_uses →
//   two tool_results → final answer. This guards against regressions in
//   the parallel-execution path that the persona's fan-out instruction
//   now relies on.

import { expect, openCaseViaApi, test } from "../fixtures/test-env";

test("fan-out tool loop handles two tool_use blocks in one turn", async ({
  page,
}) => {
  const { caseId } = await openCaseViaApi();
  await page.goto(`/app?case_id=${caseId}`);

  const textarea = page.getByPlaceholder(/Ask about this parcel/);
  const sendBtn = page.getByRole("button", { name: /^Send/ });
  const thread = page.getByTestId("chat-thread");

  // MOCK_FAN_OUT causes the dispatcher to emit two search_bylaw_evidence
  // calls in a single assistant response. The tool loop executes both in
  // parallel, returns both results as one tool_result turn, and then the
  // dispatcher answers normally on the follow-up call.
  await expect(textarea).toBeEnabled();
  await textarea.fill("MOCK_FAN_OUT what are the height and FAR limits?");
  await sendBtn.click();

  // The turn must settle with the deterministic mock answer. Reaching it
  // means both tool_use blocks were executed, both tool_results were
  // appended in one user turn, and the synthesis call succeeded —
  // i.e. the fan-out path is end-to-end well-formed.
  await expect(async () => {
    const text = (await thread.innerText()).toLowerCase();
    expect(text).toContain("based on the bylaw evidence");
  }).toPass({ timeout: 20_000 });

  // The composer re-enables — a broken pairing or stuck loop would leave
  // it disabled.
  await expect(textarea).toBeEnabled();
});
