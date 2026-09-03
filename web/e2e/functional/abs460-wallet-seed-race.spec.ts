// Functional: ABS-460 — the wallet seed fetch must never clobber a fresher
// per-turn ``token_balance``.
//
// The chat shell seeds ``wallet`` from GET /api/billing/wallet on mount and
// then keeps it live off the per-turn ``token_balance`` SSE event. Those two
// are concurrent: the composer is enabled as soon as the shell paints, so a
// turn can settle before the seed lands. When it does, the shell used to
// paint the seed's PRE-burn balance on top of the fresher post-burn one and
// the out-of-turns state never appeared — the user saw "~3 turns left" and a
// live composer on an exhausted wallet, until they reloaded.
//
// That race is what made `make e2e` nondeterministic: out-of-tokens-refusal
// drove exactly this sequence and won or lost on a ~1ms margin depending on
// how loaded the dev server was under 4 parallel workers.
//
// This spec forces the losing ordering deterministically instead of hoping
// for it — the seed is held for two seconds and answered with a stale
// pre-burn body, so it always resolves after the drain turn's SSE.

import {
  acceptCurrentTermsAs,
  expect,
  mintTestIdentity,
  openCaseAsIdentity,
  signInAs,
  test,
} from "../auth/fixtures";
import {
  CHAT_MIN_BALANCE,
  LOW_BALANCE_WARN,
  SIGNUP_GRANT,
  SIGNUP_GRANT_TURNS,
  TOKENS_PER_TURN,
} from "../fixtures/wallet-params";

/** What GET /api/billing/wallet returns for a fresh identity — i.e. what an
 * in-flight seed issued before the drain turn carries when it lands after it. */
const PRE_BURN_WALLET = {
  balance_tokens: SIGNUP_GRANT,
  approx_turns_remaining: SIGNUP_GRANT_TURNS,
  tokens_per_turn: TOKENS_PER_TURN,
  low_balance: false,
  warn_threshold_tokens: LOW_BALANCE_WARN,
  floor_tokens: CHAT_MIN_BALANCE,
  chat_enabled: true,
  payments_enabled: false,
};

test("a wallet seed that lands after a turn does not resurrect the pre-burn balance", async ({
  context,
  page,
}) => {
  const identity = mintTestIdentity("abs460seedrace");
  await signInAs(context, identity);
  await acceptCurrentTermsAs(context, identity);
  const { caseId } = await openCaseAsIdentity(context, identity, {
    anchorLabel: `abs460seedrace-${identity.subUserId}`,
  });

  // Hold the seed until well after the drain turn has settled, then answer it
  // with the stale pre-burn body. This is the real race, pinned: the request
  // was issued before the burn, so it cannot know about it.
  await page.route("**/api/billing/wallet", async (route) => {
    await new Promise((resolve) => setTimeout(resolve, 2_000));
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify(PRE_BURN_WALLET),
    });
  });

  await page.goto(`/app?case_id=${caseId}`);
  await expect(page.getByPlaceholder(/Ask about this parcel/)).toBeEnabled({
    timeout: 10_000,
  });

  // Drain the whole grant in one turn while the seed is still in flight.
  await page
    .getByPlaceholder(/Ask about this parcel/)
    .fill("MOCK_BURN_ALL — drain the wallet");
  await page.getByRole("button", { name: /^Send/ }).click();

  await expect(page.getByTestId("chat-thread")).toContainText(
    /Draining the wallet/i,
    { timeout: 15_000 },
  );

  // The out-of-turns state must hold once the late seed lands (>2s from the
  // route handler above), not flip back to "turns left".
  const prompt = page.getByTestId("top-up-prompt");
  await expect(prompt).toBeVisible({ timeout: 10_000 });
  await expect(prompt).toContainText(/out of turns/i);
  await expect(page.getByPlaceholder(/Top up to continue/i)).toBeDisabled();

  // Give the delayed seed a further beat to resolve and re-render, then assert
  // the workspace is still in the out-of-turns state and the strip is not
  // advertising the pre-burn turn count.
  await page.waitForTimeout(2_500);
  await expect(prompt).toBeVisible();
  await expect(page.getByTestId("balance-strip")).not.toContainText(
    new RegExp(`${SIGNUP_GRANT_TURNS} turns left`),
  );
});
