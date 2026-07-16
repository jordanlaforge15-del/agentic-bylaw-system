// Functional: ABS-287 (WI-6) — per-tier max_iterations wiring.
//
// What this change does:
//   Before ABS-287, session.py called run_tool_loop without max_iterations,
//   so ALL tiers were silently capped at the default of 10 regardless of the
//   advertised round count (Quick=4-6, Standard=12-18, Complex=35-50).
//   After ABS-287, ChatSession derives max_iterations from the active
//   Tier.max_iterations (Quick=8, Standard=20, Complex=55) defined in
//   advisor.billing.packs and passes it to run_tool_loop per turn.
//
// Why this is a regression guard, not an iteration-count assertion:
//   The e2e stack drives the advisor through MockGateway (no real Anthropic
//   key). The MOCK_COMPLEX_DEEP keyword drives the mock dispatcher through
//   12 serial search rounds before answering. 12 > old hardcoded cap (10),
//   so under the old code the turn would have been force-synthesised at
//   iteration 10 and produced the truncated answer pattern (no "based on
//   the bylaw evidence" marker). With the per-tier cap (Complex=55) all
//   12 rounds run and the final synthesis answer is organic.
//
//   What IS observable end-to-end is that the chat completes with the
//   deterministic mock answer — confirming the 12-round loop ran through
//   without hitting the old cap and forcing an early synthesis. The
//   composer re-enabling is the "loop closed cleanly" sentinel.

import { expect, openCaseViaApi, test } from "../fixtures/test-env";

test("complex-tier session can exceed 10 tool-loop iterations without hitting iteration cap", async ({
  page,
}) => {
  // Open a Complex-tier case so the session's Tier.max_iterations = 55.
  const { caseId } = await openCaseViaApi({ tier: "complex" });
  await page.goto(`/app?case_id=${caseId}`);

  const textarea = page.getByPlaceholder(/Ask about this parcel/);
  const sendBtn = page.getByRole("button", { name: /^Send/ });
  const thread = page.getByTestId("chat-thread");

  // MOCK_COMPLEX_DEEP causes the dispatcher to run 12 serial
  // search_bylaw_evidence rounds. With the old hardcoded max_iterations=10
  // the loop would have been force-synthesised at round 10 and would NOT
  // produce the "based on the bylaw evidence" marker. With the new
  // Complex-tier cap (55) all 12 rounds run and the organic answer lands.
  await expect(textarea).toBeEnabled();
  await textarea.fill(
    "MOCK_COMPLEX_DEEP rezoning multi-overlay deep analysis",
  );
  await sendBtn.click();

  // The turn must settle with the deterministic mock organic answer.
  // If this times out the loop hit the old iteration cap and was
  // force-synthesised (which produces a different phrasing).
  await expect(async () => {
    const text = (await thread.innerText()).toLowerCase();
    expect(text).toContain("based on the bylaw evidence");
  }).toPass({ timeout: 30_000 });

  // The composer re-enables — a stuck or force-synthesised loop can
  // leave the UI in a broken state.
  await expect(textarea).toBeEnabled({ timeout: 5_000 });
});
