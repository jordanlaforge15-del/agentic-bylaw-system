// Functional: ABS-386 — out-of-turns refusal in the chat workspace.
//
// When the account token wallet is at/below the floor, the workspace refuses
// to send: the TopUpPrompt renders above the disabled composer (placeholder
// "Top up to continue"), no assistant stream starts, and NO generic
// "Backend error (402)" toast appears. Copy is turns-based.
//
// Two payment postures:
//   * payments OFF (the DORMANT e2e stack, driven for real): no purchase CTA,
//     and the self-serve refill claim instead (ABS-405 — a fresh account
//     still holds its refill allowance). Driven by MOCK_BURN_ALL, which
//     overdraws the signup grant in one turn. The refill's own behaviour
//     (claiming, cooldown) lives in abs405-beta-refill.spec.ts; here we only
//     pin that the refusal itself is clean.
//   * payments ON: a purchase CTA ("Top up →"). The e2e stack can't run
//     payments-on, so the wallet read the strip seeds from is stubbed.

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
  TOKENS_PER_TURN,
} from "../fixtures/wallet-params";

const OUT_OF_TOKENS_WALLET_PAYMENTS_ON = {
  balance_tokens: 0,
  approx_turns_remaining: 0,
  tokens_per_turn: TOKENS_PER_TURN,
  low_balance: true,
  warn_threshold_tokens: LOW_BALANCE_WARN,
  floor_tokens: CHAT_MIN_BALANCE,
  chat_enabled: false,
  payments_enabled: true,
};

test("payments off: draining the wallet shows the out-of-turns dead-end and disables the composer", async ({
  context,
  page,
}) => {
  const identity = mintTestIdentity("abs386ootoff");
  await signInAs(context, identity);
  await acceptCurrentTermsAs(context, identity);
  const { caseId } = await openCaseAsIdentity(context, identity, {
    anchorLabel: `abs386ootoff-${identity.subUserId}`,
  });

  await page.goto(`/app?case_id=${caseId}`);
  await expect(page.getByPlaceholder(/Ask about this parcel/)).toBeEnabled({
    timeout: 10_000,
  });

  // Drain the entire signup grant in one turn via the mock dispatcher.
  await page
    .getByPlaceholder(/Ask about this parcel/)
    .fill("MOCK_BURN_ALL — drain the wallet");
  await page.getByRole("button", { name: /^Send/ }).click();

  // The draining turn's message is NOT lost — it stays in the thread.
  await expect(page.getByTestId("chat-thread")).toContainText(
    /MOCK_BURN_ALL/i,
    { timeout: 15_000 },
  );

  // The token_balance SSE flips the workspace into the out-of-turns state.
  const prompt = page.getByTestId("top-up-prompt");
  await expect(prompt).toBeVisible({ timeout: 10_000 });
  await expect(prompt).toContainText(/out of turns/i);

  // Payments off → NO purchase CTA. A first-time exhaustion still has its
  // refill allowance, so the prompt offers the self-serve claim (ABS-405)
  // rather than the old flat dead-end.
  await expect(page.getByTestId("top-up-btn")).toHaveCount(0);
  await expect(page.getByTestId("beta-refill-btn")).toBeVisible();

  // Composer disabled with the top-up placeholder.
  await expect(page.getByPlaceholder(/Top up to continue/i)).toBeDisabled();

  // No generic backend-error toast anywhere.
  await expect(page.locator("body")).not.toContainText(/Backend error/i);
});

test("payments on: out of turns shows the top-up CTA", async ({
  context,
  page,
}) => {
  const identity = mintTestIdentity("abs386ooton");
  await signInAs(context, identity);
  await acceptCurrentTermsAs(context, identity);
  const { caseId } = await openCaseAsIdentity(context, identity, {
    anchorLabel: `abs386ooton-${identity.subUserId}`,
  });

  // Billing is dormant in e2e, so exercise the payments-ON posture by stubbing
  // the wallet read the strip seeds from.
  await page.route("**/api/billing/wallet", (route) =>
    route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify(OUT_OF_TOKENS_WALLET_PAYMENTS_ON),
    }),
  );

  await page.goto(`/app?case_id=${caseId}`);

  const prompt = page.getByTestId("top-up-prompt");
  await expect(prompt).toBeVisible({ timeout: 10_000 });
  await expect(prompt).toContainText(/out of turns/i);
  // Purchase CTA present; no dead-end copy.
  await expect(page.getByTestId("top-up-btn")).toBeVisible();
  await expect(page.getByTestId("top-up-deadend")).toHaveCount(0);
  // The strip's low-balance nudge link shows only when payments are on.
  await expect(page.getByTestId("balance-topup-link")).toBeVisible();
  // Composer disabled with the top-up placeholder.
  await expect(page.getByPlaceholder(/Top up to continue/i)).toBeDisabled();
  // Still no generic backend-error toast.
  await expect(page.locator("body")).not.toContainText(/Backend error/i);
});
