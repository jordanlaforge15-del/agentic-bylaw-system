// Functional: ABS-290 (WI-4) — in-flight tool-result compaction.
//
// What this change does (backend cost optimization):
//   The tool loop (src/advisor/llm/tool_loop.py) now summarises OLDER
//   tool_result turns inside a single in-flight question's loop, keeping
//   only the most recent K (default 3) full. On a deep question that
//   fans out into many retrieval rounds, the early rounds' payloads are
//   replaced with the same deterministic one-line summaries the
//   submission-path compactor uses, shrinking the cached-but-billed tail
//   and the per-iteration cache writes. The rewrite happens strictly
//   before the rolling cache breakpoint (ABS-285), which keeps riding
//   the latest, still-full tool_result.
//
// Why this is a regression guard, not a cost assertion:
//   The e2e stack drives the advisor through MockGateway, which ignores
//   cache_control flags (cache reads only bill against the real Anthropic
//   backend), so the byte-level savings aren't observable here. The
//   byte-level placement and determinism are pinned in the Python unit
//   tests (tests/advisor/chat/test_history_compaction.py — summaries,
//   prefix stability, kill-switch; tests/advisor/llm/test_tool_loop.py —
//   the loop rewrites older tool_results in the request while the
//   returned conversation keeps full payloads).
//
//   What IS observable end-to-end is the load-bearing safety property:
//   rewriting tool_result bytes in the middle of a live loop must not
//   break the Messages-API invariant that every tool_use has a matching
//   tool_result. The MOCK_DEEP_SEARCH keyword fans one turn out into 5
//   serial search rounds — past the compaction window — so the loop
//   actually summarises its early rounds before synthesising. This guard
//   confirms that deep, compacted loop still streams a correct, complete
//   answer. Against real Anthropic a broken pairing would surface as a
//   400 on the round after the first summary; this catches that locally.

import { expect, openCaseViaApi, test } from "../fixtures/test-env";

test("deep tool loop still answers after older tool_results are compacted", async ({
  page,
}) => {
  const { caseId } = await openCaseViaApi();
  await page.goto(`/app?case_id=${caseId}`);

  const textarea = page.getByPlaceholder(/Ask about this parcel/);
  const sendBtn = page.getByRole("button", { name: /^Send/ });
  const thread = page.getByTestId("chat-thread");

  // MOCK_DEEP_SEARCH drives 5 serial search rounds in this single turn.
  // With the default in-loop keep-recent window of 3, the loop summarises
  // the two oldest rounds' tool_results before the final synthesis call —
  // so this answer is produced from a compacted, multi-round conversation.
  await expect(textarea).toBeEnabled();
  await textarea.fill("MOCK_DEEP_SEARCH what is the maximum building height?");
  await sendBtn.click();

  // The turn must settle with the deterministic mock answer. Reaching it
  // means all five rounds ran, the older tool_results were rewritten to
  // summaries mid-loop, and every tool_use still had a matching
  // tool_result — i.e. the compacted request stayed well-formed.
  await expect(async () => {
    const text = (await thread.innerText()).toLowerCase();
    expect(text).toContain("based on the bylaw evidence");
  }).toPass({ timeout: 25_000 });

  // The composer re-enables once the deep loop completes — a stuck or
  // rejected mid-loop request would leave it disabled.
  await expect(textarea).toBeEnabled();
});
