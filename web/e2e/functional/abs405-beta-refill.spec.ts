// Functional: ABS-405 — self-serve way out of an overdrawn wallet.
//
// Before this, a beta tester who drained their wallet had no path back into
// chat: the out-of-turns prompt said "paid top-ups are coming soon" and the
// only fix was an operator running grant_tokens by hand. Every stuck tester
// was a support touch.
//
// The chat cases are driven against the real DORMANT (payments-off) e2e
// stack — no stubbing — because that is the posture the feature exists for:
//   1. MOCK_BURN_ALL overdraws the signup grant in one turn.
//   2. The out-of-turns prompt offers "Add more turns"; one click credits a
//      refill and the composer re-enables in place, no reload.
//   3. Draining again inside the cooldown does NOT offer a second claim —
//      it says when more turns unlock, so the user waits instead of
//      emailing support.
//
// Then the guard the other way: when payments are ON the refill is not
// offered at all (the top-up checkout is the path). That posture can't run
// for real on the e2e stack, so the wallet read is stubbed.
//
// Finally the two other surfaces that render an exhausted wallet — the
// case-open form and the billing page — because a fix that reached only the
// composer would still dead-end the same user one screen over.

import {
  acceptCurrentTermsAs,
  expect,
  mintTestIdentity,
  openCaseAsIdentity,
  signInAs,
  test,
} from "../auth/fixtures";
import {
  BETA_REFILL_COOLDOWN_HOURS,
  BETA_REFILL_TOKENS,
  BETA_REFILL_TURNS,
  CHAT_MIN_BALANCE,
  LOW_BALANCE_WARN,
  SIGNUP_GRANT_TURNS,
  TOKENS_PER_TURN,
} from "../fixtures/wallet-params";

const ASK = /Ask about this parcel/;

async function drainWallet(page: import("@playwright/test").Page) {
  await page.getByPlaceholder(ASK).fill("MOCK_BURN_ALL — drain the wallet");
  await page.getByRole("button", { name: /^Send/ }).click();
  await expect(page.getByTestId("top-up-prompt")).toBeVisible({
    timeout: 20_000,
  });
}

test("payments off: an overdrawn user claims a refill and keeps working", async ({
  context,
  page,
}) => {
  const identity = mintTestIdentity("abs405refill");
  await signInAs(context, identity);
  await acceptCurrentTermsAs(context, identity);
  const { caseId } = await openCaseAsIdentity(context, identity, {
    anchorLabel: `abs405refill-${identity.subUserId}`,
  });

  await page.goto(`/app?case_id=${caseId}`);
  await expect(page.getByPlaceholder(ASK)).toBeEnabled({ timeout: 10_000 });

  await drainWallet(page);

  // The dead-end is gone: there is a self-serve offer, and it says how many
  // turns the claim is worth (backend-owned figure, not a hardcoded string).
  const offer = page.getByTestId("beta-refill-offer");
  await expect(offer).toBeVisible();
  await expect(offer).toContainText(
    BETA_REFILL_TURNS === 1 ? /another turn/i : /more turns/i,
  );
  await expect(page.getByTestId("top-up-deadend")).toHaveCount(0);
  // Payments are off — no purchase CTA sneaks in alongside the refill.
  await expect(page.getByTestId("top-up-btn")).toHaveCount(0);
  await expect(page.getByPlaceholder(/Top up to continue/i)).toBeDisabled();

  // One click puts the account back in business — in place, no reload.
  await page.getByTestId("beta-refill-btn").click();
  await expect(page.getByTestId("top-up-prompt")).toHaveCount(0, {
    timeout: 15_000,
  });
  await expect(page.getByPlaceholder(ASK)).toBeEnabled();

  // And chat genuinely works again — the refill is real tokens, not a UI
  // state flip.
  await page
    .getByPlaceholder(ASK)
    .fill("What is the minimum front yard setback?");
  await page.getByRole("button", { name: /^Send/ }).click();
  await expect(page.getByTestId("chat-thread")).toContainText(
    /Based on the bylaw evidence/i,
    { timeout: 20_000 },
  );
  await expect(page.locator("body")).not.toContainText(/Backend error/i);
});

test("payments off: a second drain inside the cooldown says when turns unlock", async ({
  context,
  page,
}) => {
  const identity = mintTestIdentity("abs405cooldown");
  await signInAs(context, identity);
  await acceptCurrentTermsAs(context, identity);
  const { caseId } = await openCaseAsIdentity(context, identity, {
    anchorLabel: `abs405cooldown-${identity.subUserId}`,
  });

  await page.goto(`/app?case_id=${caseId}`);
  await expect(page.getByPlaceholder(ASK)).toBeEnabled({ timeout: 10_000 });

  await drainWallet(page);
  await page.getByTestId("beta-refill-btn").click();
  await expect(page.getByPlaceholder(ASK)).toBeEnabled({ timeout: 15_000 });

  // Burn it all again immediately — well inside the cooldown window.
  await drainWallet(page);

  // No second claim on offer, and the copy tells the user when to come back
  // rather than leaving them to guess (or to open a support ticket).
  await expect(page.getByTestId("beta-refill-btn")).toHaveCount(0);
  const deadend = page.getByTestId("top-up-deadend");
  await expect(deadend).toBeVisible();
  await expect(deadend).toContainText(/More turns unlock in/i);
  await expect(deadend).toContainText(
    new RegExp(`${BETA_REFILL_COOLDOWN_HOURS} hours`, "i"),
  );
  // The reassurance about saved history survives the added sentence.
  await expect(deadend).toContainText(/stay saved/i);
});

test("payments on: the refill is not offered — top up is the path", async ({
  context,
  page,
}) => {
  const identity = mintTestIdentity("abs405payon");
  await signInAs(context, identity);
  await acceptCurrentTermsAs(context, identity);
  const { caseId } = await openCaseAsIdentity(context, identity, {
    anchorLabel: `abs405payon-${identity.subUserId}`,
  });

  // The e2e stack can't run payments-on, so stub the wallet read the shell
  // seeds from. `beta_refill.available` is deliberately true here: the UI
  // must suppress the claim on the payments posture alone, matching the
  // backend, which refuses it outright.
  await page.route("**/api/billing/wallet", (route) =>
    route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        balance_tokens: 0,
        approx_turns_remaining: 0,
        tokens_per_turn: TOKENS_PER_TURN,
        low_balance: true,
        warn_threshold_tokens: LOW_BALANCE_WARN,
        floor_tokens: CHAT_MIN_BALANCE,
        chat_enabled: false,
        payments_enabled: true,
        beta_refill: {
          available: true,
          status: "available",
          tokens: BETA_REFILL_TOKENS,
          approx_turns: BETA_REFILL_TURNS,
          grants_remaining: 3,
          next_available_at: null,
        },
      }),
    }),
  );

  await page.goto(`/app?case_id=${caseId}`);
  await expect(page.getByTestId("top-up-prompt")).toBeVisible({
    timeout: 10_000,
  });
  await expect(page.getByTestId("top-up-btn")).toBeVisible();
  await expect(page.getByTestId("beta-refill-btn")).toHaveCount(0);
  await expect(page.getByTestId("beta-refill-offer")).toHaveCount(0);
});

// The chat prompt is not the only place a stuck user lands. The case-open
// form and the billing page render their own exhaustion copy, and a fix that
// reached only the composer would still dead-end the same person one screen
// over. Both are driven with a stubbed wallet READ (to force the exhausted
// state without burning a real wallet) and a REAL claim POST, so the wiring
// from button to backend is exercised end to end on each surface.

const EXHAUSTED_WITH_REFILL = {
  balance_tokens: 0,
  approx_turns_remaining: 0,
  tokens_per_turn: TOKENS_PER_TURN,
  low_balance: true,
  warn_threshold_tokens: LOW_BALANCE_WARN,
  floor_tokens: CHAT_MIN_BALANCE,
  chat_enabled: false,
  payments_enabled: false,
  beta_refill: {
    available: true,
    status: "available",
    tokens: BETA_REFILL_TOKENS,
    approx_turns: BETA_REFILL_TURNS,
    grants_remaining: 3,
    next_available_at: null,
  },
};

async function stubExhaustedWallet(page: import("@playwright/test").Page) {
  // Anchored on the exact path so the refill POST underneath it still
  // reaches the real backend.
  await page.route("**/api/billing/wallet", (route) =>
    route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify(EXHAUSTED_WITH_REFILL),
    }),
  );
}

test("cases/new: an exhausted user can claim before opening a case", async ({
  context,
  page,
}) => {
  const identity = mintTestIdentity("abs405casesnew");
  await signInAs(context, identity);
  await acceptCurrentTermsAs(context, identity);
  await stubExhaustedWallet(page);

  await page.goto("/cases/new");

  const notice = page.getByTestId("balance-notice");
  await expect(notice).toBeVisible({ timeout: 10_000 });
  await expect(notice).toContainText(/free trial is used up/i);
  await expect(notice).toContainText(/add .* to this account right now/i);
  // Payments are off — the billing ghost stays hidden.
  await expect(page.getByTestId("top-up-turns-btn")).toHaveCount(0);

  // Claiming here clears the attention notice outright: the returned wallet
  // has turns on it, so the page is no longer in a low/empty state.
  await page.getByTestId("beta-refill-btn").click();
  await expect(notice).toHaveCount(0, { timeout: 15_000 });
});

test("billing page: an exhausted user can claim instead of waiting on top-ups", async ({
  context,
  page,
}) => {
  const identity = mintTestIdentity("abs405billing");
  await signInAs(context, identity);
  await acceptCurrentTermsAs(context, identity);
  await stubExhaustedWallet(page);

  await page.goto("/app/billing");

  const banner = page.getByTestId("billing-beta-banner");
  await expect(banner).toBeVisible({ timeout: 15_000 });
  await expect(banner).toContainText(/add .* to this account right now/i);

  await page.getByTestId("billing-beta-refill-btn").click();
  // After the claim the offer is spent for now, and the banner says when the
  // next one unlocks rather than reverting to a bare "open soon".
  await expect(page.getByTestId("billing-beta-refill-btn")).toHaveCount(0, {
    timeout: 15_000,
  });
  await expect(banner).toContainText(/More turns unlock in/i);
  // The headline now reflects the real wallet the claim returned: this
  // identity's untouched signup grant plus the turn it just claimed. The
  // stubbed "0 turns" is gone, which is the whole point.
  await expect(page.getByTestId("billing-turns")).toContainText(
    `~${SIGNUP_GRANT_TURNS + BETA_REFILL_TURNS} turns`,
  );
});
