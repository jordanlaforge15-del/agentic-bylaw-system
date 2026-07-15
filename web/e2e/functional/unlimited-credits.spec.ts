// Functional: ABS-390 — token-wallet balance bypass for unlimited users.
//
// Reconciled from the old ABS-12 unlimited-CaseCredit spec to the beta pivot
// (docs/decisions/2026-07-beta-pivot-turn-wallet-gated-reports.md, D3): chat is
// billed per turn out of the token wallet and refused pre-flight at balance ≤
// floor (402 insufficient_tokens) — EXCEPT for `unlimited_credits` users, who
// bypass the floor entirely and are exempt from the per-turn burn.
//
// This spec verifies the bypass by contrast:
//   * a normal user drained below the floor (via MOCK_BURN_ALL, which overdraws
//     the signup grant in one turn) is refused on the next turn (402);
//   * an unlimited_credits user, hit by the same drain, is NEVER refused and
//     NEVER burns — the wallet reports chat_enabled=true at any balance.
//   * opening a case stays free for both (ABS-382), no CaseCredit reserved.
//
// Per-run user ids so nothing races the shared demo user. Calls FastAPI
// directly (X-Test-User-Id): the Next proxy binds the upstream user id to
// ADVISOR_DEMO_USER_ID at process start, so per-user API tests bypass it.

import { execSync } from "node:child_process";
import * as path from "node:path";

import { expect, test } from "@playwright/test";

import { E2E_API_URL } from "../fixtures/test-env";

const UNLIMITED_USER = `unlimited-${Date.now()}-${Math.random()
  .toString(36)
  .slice(2, 8)}`;
const NORMAL_USER = `normal-${Date.now()}-${Math.random()
  .toString(36)
  .slice(2, 8)}`;

type Req = import("@playwright/test").APIRequestContext;

test.beforeAll(() => {
  const repoRoot = path.resolve(__dirname, "..", "..", "..");
  const seed = path.join(repoRoot, "scripts", "seed_e2e_user.py");
  const venvPython = path.join(repoRoot, ".venv", "bin", "python");
  const pgPort = process.env.PG_PORT || "5432";
  const databaseUrl =
    process.env.DATABASE_URL ||
    `postgresql+psycopg://layer1:layer1@localhost:${pgPort}/layer1_test`;
  const seedEnv = {
    ...process.env,
    DATABASE_URL: databaseUrl,
    PYTHONPATH: `${path.join(repoRoot, "src")}:${process.env.PYTHONPATH || ""}`,
  };

  // Unlimited user: unlimited_credits=True. --token-balance 0 leaves the
  // wallet untouched by the seed so the one-time signup grant is the only
  // funding — proving the bypass is the flag, not a stockpile.
  execSync(
    `"${venvPython}" "${seed}" --user-id "${UNLIMITED_USER}" ` +
      `--email "${UNLIMITED_USER}@e2e.test" --token-balance 0 --unlimited`,
    { env: seedEnv, stdio: "inherit" },
  );
  // Normal user: default posture, wallet funded only by the signup grant.
  execSync(
    `"${venvPython}" "${seed}" --user-id "${NORMAL_USER}" ` +
      `--email "${NORMAL_USER}@e2e.test" --token-balance 0`,
    { env: seedEnv, stdio: "inherit" },
  );
});

async function openCase(request: Req, userId: string): Promise<number> {
  const res = await request.post(`${E2E_API_URL}/v1/cases`, {
    headers: { "X-Test-User-Id": userId },
    data: {
      anchor_label: `bypass-${userId}-${Date.now()}`,
      anchor_kind: "address",
      tier: "standard",
    },
  });
  expect(res.status(), `open_case: ${res.status()} ${await res.text()}`).toBe(
    200,
  );
  const body = (await res.json()) as {
    credit_id: number | null;
    case: { id: number };
  };
  // ABS-382: opening a case is free — no CaseCredit reserved for anyone.
  expect(body.credit_id).toBeNull();
  return body.case.id;
}

async function chat(
  request: Req,
  userId: string,
  caseId: number,
  message: string,
) {
  return request.post(`${E2E_API_URL}/v1/chat`, {
    headers: {
      "X-Test-User-Id": userId,
      "Content-Type": "application/json",
      Accept: "text/event-stream",
    },
    data: { message, case_id: caseId, session_id: null },
    timeout: 15_000,
  });
}

async function wallet(request: Req, userId: string) {
  const res = await request.get(`${E2E_API_URL}/v1/billing/wallet`, {
    headers: { "X-Test-User-Id": userId },
  });
  expect(res.status(), await res.text()).toBe(200);
  return res.json() as Promise<{
    balance_tokens: number;
    chat_enabled: boolean;
    low_balance: boolean;
  }>;
}

test("normal user drained below the floor is refused on the next turn (402)", async ({
  request,
}) => {
  const caseId = await openCase(request, NORMAL_USER);

  // First turn passes pre-flight (25k grant > floor) and overdraws the wallet.
  const drain = await chat(
    request,
    NORMAL_USER,
    caseId,
    "MOCK_BURN_ALL — drain the wallet",
  );
  expect(drain.status(), await drain.text()).toBe(200);

  // The wallet is now below the floor: chat is refused pre-flight.
  const after = await wallet(request, NORMAL_USER);
  expect(after.chat_enabled).toBe(false);

  const refused = await chat(
    request,
    NORMAL_USER,
    caseId,
    "What is the minimum front yard setback?",
  );
  expect(refused.status(), await refused.text()).toBe(402);
  const body = (await refused.json()) as { detail: { code: string } };
  expect(body.detail.code).toBe("insufficient_tokens");
});

test("unlimited_credits user bypasses the floor — chats freely, never burns", async ({
  request,
}) => {
  const caseId = await openCase(request, UNLIMITED_USER);

  const before = await wallet(request, UNLIMITED_USER);
  expect(before.chat_enabled).toBe(true);
  expect(before.low_balance).toBe(false);

  // The same MOCK_BURN_ALL turn that overdraws a normal user does not touch an
  // unlimited user's balance (exempt from the burn).
  const burn = await chat(
    request,
    UNLIMITED_USER,
    caseId,
    "MOCK_BURN_ALL — would drain a normal wallet",
  );
  expect(burn.status(), await burn.text()).toBe(200);

  const after = await wallet(request, UNLIMITED_USER);
  expect(
    after.balance_tokens,
    "unlimited users are exempt from the per-turn burn",
  ).toBe(before.balance_tokens);
  expect(after.chat_enabled).toBe(true);
  expect(after.low_balance).toBe(false);

  // And a follow-up turn still streams — never refused regardless of balance.
  const again = await chat(
    request,
    UNLIMITED_USER,
    caseId,
    "What is the minimum front yard setback?",
  );
  const againBody = await again.text();
  expect(again.status(), againBody.slice(0, 300)).toBe(200);
  expect(againBody).toMatch(/text_delta/);
});
