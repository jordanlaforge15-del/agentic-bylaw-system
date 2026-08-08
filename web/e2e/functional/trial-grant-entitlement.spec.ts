// Functional: ABS-390 — the signup grant is now a TOKEN-wallet grant.
//
// Reconciled from the old ABS-314 free-question / CaseCredit trial grant to the
// beta pivot (docs/decisions/2026-07-beta-pivot-turn-wallet-gated-reports.md,
// D2): a brand-new user's wallet receives a one-time ADVISOR_SIGNUP_TOKEN_GRANT
// (rate recalibrated on ABS-416, grant resized to ~3 turns on ABS-404 against
// the measured cost anchor) the first time they authenticate, lazily
// in resolve_or_create_user and idempotent on the `token_grant_issued` flag.
//
// This spec verifies:
//   1. A brand-new user (never seeded) is auto-created and granted the signup
//      tokens on their first authenticated request — GET /v1/billing/wallet
//      shows the full grant, funded and chat-enabled.
//   2. The grant is one-time: a second read returns the same balance (no
//      double-grant), and burning is a separate concern.
//   3. GET /v1/billing/me echoes the same token_balance.
//
// Fresh per-run user ids so nothing races the shared demo user. Calls FastAPI
// directly (X-Test-User-Id) — the grant fires in the header-fallback resolver
// exactly as it does for a real Clerk first-login.

import { expect, test } from "@playwright/test";

import { E2E_API_URL } from "../fixtures/test-env";

// Calibrated defaults (advisor.billing.turns / the e2e env). The stack does
// not override these, so the code defaults are authoritative.
import {
  LOW_BALANCE_WARN as WARN,
  SIGNUP_GRANT,
  SIGNUP_GRANT_TURNS,
  TOKENS_PER_TURN,
} from "../fixtures/wallet-params";

function freshUser(tag: string): string {
  return `trialgrant-${tag}-${Date.now()}-${Math.random()
    .toString(36)
    .slice(2, 8)}`;
}

test("brand-new user is granted the signup token wallet on first request", async ({
  request,
}) => {
  const userId = freshUser("new");

  const res = await request.get(`${E2E_API_URL}/v1/billing/wallet`, {
    headers: { "X-Test-User-Id": userId },
  });
  expect(res.status(), await res.text()).toBe(200);

  const body = (await res.json()) as {
    balance_tokens: number;
    approx_turns_remaining: number;
    tokens_per_turn: number;
    low_balance: boolean;
    chat_enabled: boolean;
    warn_threshold_tokens: number;
  };

  expect(body.balance_tokens, "signup grant funds the wallet").toBe(
    SIGNUP_GRANT,
  );
  // Backend-owned turns conversion: floor(525000 / 175000) == 3.
  expect(body.tokens_per_turn).toBe(TOKENS_PER_TURN);
  expect(body.approx_turns_remaining).toBe(SIGNUP_GRANT_TURNS);
  // The grant is well above the warn threshold: the trial user starts healthy.
  expect(body.warn_threshold_tokens).toBe(WARN);
  expect(body.low_balance).toBe(false);
  expect(body.chat_enabled).toBe(true);
});

test("the signup grant is one-time: a second read does not double it", async ({
  request,
}) => {
  const userId = freshUser("idempotent");

  const first = await request.get(`${E2E_API_URL}/v1/billing/wallet`, {
    headers: { "X-Test-User-Id": userId },
  });
  expect(first.status()).toBe(200);
  expect((await first.json()).balance_tokens).toBe(SIGNUP_GRANT);

  // Second authenticated request re-runs resolve_or_create_user, which is a
  // no-op once `token_grant_issued` is set — the balance is unchanged.
  const second = await request.get(`${E2E_API_URL}/v1/billing/wallet`, {
    headers: { "X-Test-User-Id": userId },
  });
  expect(second.status()).toBe(200);
  expect(
    (await second.json()).balance_tokens,
    "no second grant on a repeat request",
  ).toBe(SIGNUP_GRANT);
});

test("GET /billing/me echoes the granted token_balance", async ({
  request,
}) => {
  const userId = freshUser("me");

  const res = await request.get(`${E2E_API_URL}/v1/billing/me`, {
    headers: { "X-Test-User-Id": userId },
  });
  expect(res.status(), await res.text()).toBe(200);
  const body = (await res.json()) as { token_balance: number };
  expect(body.token_balance).toBe(SIGNUP_GRANT);
});
