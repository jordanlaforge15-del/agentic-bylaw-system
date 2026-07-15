// Functional: ABS-386 — top-up checkout from the out-of-turns TopUpPrompt.
//
// Payments-on flow: clicking "Top up →" starts a checkout (POST
// /api/billing/checkout/topup → Stripe URL) and redirects the browser to it.
// On return from a completed payment, the /app mount refetches the wallet and
// the composer re-enables.
//
// The e2e stack runs billing DORMANT, and there is no browser-hosted MockStripe
// page in the suite, so this spec stubs the payments-on wallet, the checkout
// start (returns a MockStripe URL), and the MockStripe hosted page (a Pay
// button that redirects back to /app) via page.route. This exercises the
// FRONTEND checkout→return behaviour; the server-side checkout/webhook crediting
// is covered by the ABS-381 unit/integration suite.

import {
  acceptCurrentTermsAs,
  E2E_BASE_URL,
  expect,
  mintTestIdentity,
  openCaseAsIdentity,
  signInAs,
  test,
} from "../auth/fixtures";

const OUT_OF_TOKENS_WALLET = {
  balance_tokens: 0,
  approx_turns_remaining: 0,
  tokens_per_turn: 2500,
  low_balance: true,
  warn_threshold_tokens: 5000,
  floor_tokens: 0,
  chat_enabled: false,
  payments_enabled: true,
};

const TOPPED_UP_WALLET = {
  ...OUT_OF_TOKENS_WALLET,
  balance_tokens: 20_000,
  approx_turns_remaining: 8,
  low_balance: false,
  chat_enabled: true,
};

test("payments on: top-up reaches MockStripe checkout and re-enables the composer on return", async ({
  context,
  page,
}) => {
  const identity = mintTestIdentity("abs386topup");
  await signInAs(context, identity);
  await acceptCurrentTermsAs(context, identity);
  const { caseId } = await openCaseAsIdentity(context, identity, {
    anchorLabel: `abs386topup-${identity.subUserId}`,
  });

  // The wallet starts out of turns (payments on) and reflects the top-up after
  // "payment". The route closure reads the current `walletBody` each call.
  let walletBody: typeof OUT_OF_TOKENS_WALLET = OUT_OF_TOKENS_WALLET;
  await page.route("**/api/billing/wallet", (route) =>
    route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify(walletBody),
    }),
  );

  // Checkout start returns a MockStripe hosted-checkout URL.
  const CHECKOUT_URL = `${E2E_BASE_URL}/e2e-mock-stripe-topup`;
  await page.route("**/api/billing/checkout/topup", (route) =>
    route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({ url: CHECKOUT_URL }),
    }),
  );

  // MockStripe hosted page: a Pay button that "completes" the payment and
  // redirects back to /app for this case.
  await page.route("**/e2e-mock-stripe-topup", (route) =>
    route.fulfill({
      status: 200,
      contentType: "text/html",
      body:
        `<!doctype html><html><body><h1>MockStripe Checkout</h1>` +
        `<a data-testid="mock-pay" href="${E2E_BASE_URL}/app?case_id=${caseId}">Pay</a>` +
        `</body></html>`,
    }),
  );

  await page.goto(`/app?case_id=${caseId}`);
  const topUpBtn = page.getByTestId("top-up-btn");
  await expect(topUpBtn).toBeVisible({ timeout: 10_000 });

  // Click "Top up →" → the browser reaches the MockStripe checkout page.
  await topUpBtn.click();
  await expect(page.getByTestId("mock-pay")).toBeVisible({ timeout: 10_000 });

  // Payment completes; the wallet now reflects the top-up before we return.
  walletBody = TOPPED_UP_WALLET;
  await page.getByTestId("mock-pay").click();

  // Back on /app, the composer re-enables after the balance refetch and the
  // out-of-turns prompt is gone; the strip shows the topped-up turns.
  await expect(page.getByPlaceholder(/Ask about this parcel/i)).toBeEnabled({
    timeout: 10_000,
  });
  await expect(page.getByTestId("top-up-prompt")).toHaveCount(0);
  await expect(page.getByTestId("balance-strip")).toContainText(
    /~8 turns left/i,
  );
});
