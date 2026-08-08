// Functional: ABS-380 — token wallet foundation, proxy contract.
//
// A fresh authenticated user is granted the signup token wallet on first
// sign-in (self-heal in resolve_or_create_user / the e2e header path). This
// spec drives the Next.js proxy end to end (browser → /api/billing/* →
// FastAPI) and asserts:
//   1. GET /api/billing/me surfaces the granted token_balance additively,
//      with the legacy credit fields intact.
//   2. GET /api/billing/wallet returns the turns-aware shape with
//      backend-owned conversion (approx_turns_remaining == floor(balance /
//      tokens_per_turn)) and the threshold/flag fields.
//   3. GET /api/billing/wallet/transactions shows the signup grant row.
//
// Each run mints a unique identity so it never races the shared demo user's
// wallet. The e2e stack runs billing DORMANT (ADVISOR_BILLING_ENABLED
// unset), so the wallet is served by the dormant router with
// payments_enabled == false — proving the wallet read surface is mounted on
// both router flavours.

import {
  expect,
  mintTestIdentity,
  signInAs,
  test,
} from "../auth/fixtures";

// Design-spec defaults, which the e2e FastAPI env does not override.
import {
  SIGNUP_GRANT,
  TOKENS_PER_TURN,
} from "../fixtures/wallet-params";

test("fresh authed user: granted wallet via /api/billing proxy", async ({
  context,
}) => {
  const identity = mintTestIdentity("abs380");
  await signInAs(context, identity);

  // 1. /api/billing/me carries the granted token_balance additively.
  const meRes = await context.request.get("/api/billing/me");
  expect(
    meRes.status(),
    `me expected 200: ${await meRes.text()}`,
  ).toBe(200);
  const me = (await meRes.json()) as {
    token_balance: number;
    free_questions_remaining: number;
    tier_balances: unknown[];
  };
  expect(me.token_balance).toBe(SIGNUP_GRANT);
  // Legacy fields intact.
  expect(me).toHaveProperty("free_questions_remaining");
  expect(Array.isArray(me.tier_balances)).toBe(true);

  // 2. /api/billing/wallet — turns-aware shape.
  const walletRes = await context.request.get("/api/billing/wallet");
  expect(
    walletRes.status(),
    `wallet expected 200: ${await walletRes.text()}`,
  ).toBe(200);
  const wallet = (await walletRes.json()) as {
    balance_tokens: number;
    approx_turns_remaining: number;
    tokens_per_turn: number;
    low_balance: boolean;
    warn_threshold_tokens: number;
    floor_tokens: number;
    chat_enabled: boolean;
    payments_enabled: boolean;
  };
  expect(wallet.balance_tokens).toBe(SIGNUP_GRANT);
  expect(wallet.tokens_per_turn).toBe(TOKENS_PER_TURN);
  // Turns conversion is backend-owned: floor(balance / tokens_per_turn).
  expect(wallet.approx_turns_remaining).toBe(
    Math.floor(wallet.balance_tokens / wallet.tokens_per_turn),
  );
  expect(wallet.chat_enabled).toBe(true); // balance well above floor
  expect(wallet.low_balance).toBe(false); // grant > warn threshold
  expect(typeof wallet.warn_threshold_tokens).toBe("number");
  expect(typeof wallet.floor_tokens).toBe("number");
  // Billing is dormant in e2e → top-ups not purchasable yet.
  expect(wallet.payments_enabled).toBe(false);

  // Balance is consistent across the two surfaces.
  expect(wallet.balance_tokens).toBe(me.token_balance);

  // 3. /api/billing/wallet/transactions shows the signup grant.
  const txRes = await context.request.get(
    "/api/billing/wallet/transactions?limit=10",
  );
  expect(txRes.status()).toBe(200);
  const tx = (await txRes.json()) as {
    transactions: Array<{
      entry_type: string;
      amount_tokens: number;
      balance_after: number;
      reason: string | null;
    }>;
    next_before_id: number | null;
  };
  const grant = tx.transactions.find((t) => t.entry_type === "grant");
  expect(grant, "signup grant row should be present").toBeTruthy();
  expect(grant?.amount_tokens).toBe(SIGNUP_GRANT);
  expect(grant?.balance_after).toBe(SIGNUP_GRANT);
});
