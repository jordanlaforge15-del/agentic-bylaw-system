// Functional: ABS-415 — the signup token grant lands exactly once, even when
// a fresh sign-in fires several authenticated requests at the same instant.
//
// Production, 2026-07-17: two `grant` rows of +25,000 for the same user with
// identical timestamps (ledger ids 1 and 2). `grant_signup_tokens_if_needed`
// gated on an in-memory read of `metadata_json['token_grant_issued']`, so two
// concurrent requests both saw the flag unset and both granted — the user
// silently received double the free trial.
//
// The fix is two layers, and this spec covers the pair from the outside:
//   * ABS-404 serialised the check-and-set on the user row (SELECT … FOR
//     UPDATE + populate_existing).
//   * ABS-415 put the rule in the schema — a partial unique index on
//     (user_id) WHERE entry_type='grant' AND reason='signup_grant' — so a
//     second grant row is impossible however it is written, and the service
//     absorbs the violation into "already granted" rather than a 500.
//
// `trial-grant-entitlement.spec.ts` covers the *sequential* case (a second
// read doesn't double the balance). This one covers the concurrent case that
// actually happened, over the wire, through the real resolver.
//
// Calls FastAPI directly (X-Test-User-Id) — the grant fires in the
// header-fallback resolver exactly as it does on a real Clerk first-login.

import { expect, test } from "@playwright/test";

import { E2E_API_URL } from "../fixtures/test-env";
import { SIGNUP_GRANT } from "../fixtures/wallet-params";

const PARALLEL_REQUESTS = 6;

function freshUser(tag: string): string {
  return `abs415-${tag}-${Date.now()}-${Math.random()
    .toString(36)
    .slice(2, 8)}`;
}

type WalletBody = { balance_tokens: number };
type TxnBody = {
  transactions: Array<{
    entry_type: string;
    amount_tokens: number;
    reason: string | null;
  }>;
};

test("simultaneous first requests grant the signup wallet exactly once", async ({
  request,
}) => {
  const userId = freshUser("race");

  // Fire them together, not in sequence: `request.get` returns a promise as
  // soon as it is called, so these are all in flight before any resolves —
  // which is the whole point. Awaiting them one at a time would let each
  // grant commit before the next started and the race would never happen.
  const responses = await Promise.all(
    Array.from({ length: PARALLEL_REQUESTS }, () =>
      request.get(`${E2E_API_URL}/v1/billing/wallet`, {
        headers: { "X-Test-User-Id": userId },
      }),
    ),
  );

  // Every racer that got an answer must see one grant's worth — never two.
  //
  // Non-200s are tolerated here, and in practice most of the burst is: a
  // brand-new clerk_user_id means every racer INSERTs into advisor_user and
  // all but one trip UNIQUE(clerk_user_id), which resolve_or_create_user
  // does not absorb — they 5xx. That is a real but SEPARATE defect (a failed
  // request, not a doubled wallet) and deliberately not what this spec pins;
  // it does mean the burst is largely serialised at signup, which is why the
  // sibling test below covers the self-heal path the production incident
  // actually took, and why the sharp-edged concurrency coverage lives in the
  // Postgres-gated tests/advisor/db/test_wallet_concurrency_pg.py.
  const ok = responses.filter((r) => r.status() === 200);
  expect(ok.length, "at least one concurrent request must succeed").toBeGreaterThan(0);
  for (const res of ok) {
    const body = (await res.json()) as WalletBody;
    expect(
      body.balance_tokens,
      "a concurrent racer saw a doubled (or partial) signup grant",
    ).toBe(SIGNUP_GRANT);
  }

  // Settled state is the real assertion: the wallet holds one grant, and the
  // ledger behind it has exactly one grant row. Balance alone would not catch
  // a double-grant that a later burn happened to mask.
  const wallet = await request.get(`${E2E_API_URL}/v1/billing/wallet`, {
    headers: { "X-Test-User-Id": userId },
  });
  expect(wallet.status(), await wallet.text()).toBe(200);
  expect((await wallet.json()).balance_tokens).toBe(SIGNUP_GRANT);

  const txns = await request.get(
    `${E2E_API_URL}/v1/billing/wallet/transactions`,
    { headers: { "X-Test-User-Id": userId } },
  );
  expect(txns.status(), await txns.text()).toBe(200);
  const ledger = (await txns.json()) as TxnBody;
  const grants = ledger.transactions.filter((t) => t.entry_type === "grant");
  expect(
    grants.length,
    `signup grant fired ${grants.length} times under ${PARALLEL_REQUESTS} ` +
      "concurrent first requests",
  ).toBe(1);
  expect(grants[0].amount_tokens).toBe(SIGNUP_GRANT);
  expect(grants[0].reason).toBe("signup_grant");
});

test("a repeat burst on an established user never issues a second grant", async ({
  request,
}) => {
  const userId = freshUser("settled");

  // Establish the user and its grant first, so this burst exercises the
  // self-heal path (`grant_signup_tokens_if_needed` on an existing user)
  // rather than the create path. That is the code that runs on EVERY
  // authenticated request for the rest of the account's life, so a
  // regression there re-grants forever, not just at signup.
  const first = await request.get(`${E2E_API_URL}/v1/billing/wallet`, {
    headers: { "X-Test-User-Id": userId },
  });
  expect(first.status(), await first.text()).toBe(200);
  expect((await first.json()).balance_tokens).toBe(SIGNUP_GRANT);

  const burst = await Promise.all(
    Array.from({ length: PARALLEL_REQUESTS }, () =>
      request.get(`${E2E_API_URL}/v1/billing/wallet`, {
        headers: { "X-Test-User-Id": userId },
      }),
    ),
  );
  for (const res of burst) {
    expect(res.status(), await res.text()).toBe(200);
    expect((await res.json()).balance_tokens).toBe(SIGNUP_GRANT);
  }

  const txns = await request.get(
    `${E2E_API_URL}/v1/billing/wallet/transactions`,
    { headers: { "X-Test-User-Id": userId } },
  );
  expect(txns.status()).toBe(200);
  const ledger = (await txns.json()) as TxnBody;
  expect(
    ledger.transactions.filter((t) => t.entry_type === "grant").length,
  ).toBe(1);
});
