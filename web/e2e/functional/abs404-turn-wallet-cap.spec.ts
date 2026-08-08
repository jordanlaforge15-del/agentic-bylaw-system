// Functional: ABS-404 — a single chat turn cannot bury the wallet.
//
// The defect:
//   The wallet burns MEASURED input + output (`_settle_token_burn`). The
//   two cost breakers that existed before this ticket are pre-flight
//   ESTIMATES of INPUT tokens only, and the char heuristic (4 chars per
//   token) under-counts JSON tool_result payloads. Nothing reconciled
//   the units, so nothing bounded what one turn could take out of a
//   wallet: production recorded a turn that never tripped the 165k
//   cumulative ceiling yet burned 247,566 wallet tokens. With the
//   pre-flight floor at 0, a user holding a single token may start such
//   a turn — which is why the original ABS-404 report saw a brand-new
//   account locked out of chat after one question.
//
//   ABS-416 fixed the ratio (a turn is 175k, not 2.5k, and the grant is
//   10 turns' worth). It did not fix the unbounded burn underneath.
//
// What this pins:
//   MOCK_WALLET_CAP_TRIP drives rounds that each carry a TINY payload
//   and a LARGE reported usage (80k in + 20k out) — the shape no
//   estimator can see, by construction. Only the measured wallet breaker
//   can end that turn.
//
//   Two assertions, and the second is the one that matters to the
//   ticket. The first says the right breaker fired; the second says the
//   turn's burn — read off the `token_balance` SSE the UI decrements
//   from, in the wallet's own numbers — is actually BOUNDED. A
//   regression that deletes the breaker leaves the mock running to its
//   12-round hard cap and burning ~1.2M tokens, roughly seven turns'
//   worth out of a ten-turn grant, in one question.
//
//   Calls FastAPI directly (not the Next.js proxy) — same reason as
//   abs305-cumulative-cost-circuit.spec.ts: the proxy pins X-Test-User-Id.

import { execSync } from "node:child_process";
import * as path from "node:path";

import { expect, test } from "@playwright/test";

import { E2E_API_URL } from "../fixtures/test-env";
import {
  CHAT_MIN_BALANCE,
  SIGNUP_GRANT,
  TOKENS_PER_TURN,
  TURN_MAX_WALLET_TOKENS,
} from "../fixtures/wallet-params";

const TEST_USER_ID = `abs404-${Date.now()}-${Math.random()
  .toString(36)
  .slice(2, 8)}`;

test.beforeAll(() => {
  const repoRoot = path.resolve(__dirname, "..", "..", "..");
  const seed = path.join(repoRoot, "scripts", "seed_e2e_user.py");
  const venvPython = path.join(repoRoot, ".venv", "bin", "python");
  const pgPort = process.env.PG_PORT || "5433";
  const databaseUrl =
    process.env.DATABASE_URL ||
    `postgresql+psycopg://layer1:layer1@localhost:${pgPort}/layer1_test`;
  execSync(
    `"${venvPython}" "${seed}" --user-id "${TEST_USER_ID}" ` +
      `--email "${TEST_USER_ID}@e2e.test" --credits-per-tier 5`,
    {
      env: {
        ...process.env,
        DATABASE_URL: databaseUrl,
        PYTHONPATH: `${path.join(repoRoot, "src")}:${
          process.env.PYTHONPATH || ""
        }`,
      },
      stdio: "inherit",
    },
  );
});

function isRecord(v: unknown): v is Record<string, unknown> {
  return typeof v === "object" && v !== null;
}

async function openCase(
  request: import("@playwright/test").APIRequestContext,
): Promise<number> {
  const res = await request.post(`${E2E_API_URL}/v1/cases`, {
    headers: { "X-Test-User-Id": TEST_USER_ID },
    data: {
      anchor_label: "200 Barrington Street, Halifax (ABS-404)",
      anchor_kind: "address",
    },
  });
  expect(
    res.status(),
    `open_case failed: ${res.status()} ${await res.text()}`,
  ).toBe(200);
  const body = (await res.json()) as { case: { id: number } };
  return body.case.id;
}

async function chatSse(
  request: import("@playwright/test").APIRequestContext,
  caseId: number,
  message: string,
): Promise<string> {
  const res = await request.post(`${E2E_API_URL}/v1/chat`, {
    headers: {
      "X-Test-User-Id": TEST_USER_ID,
      "Content-Type": "application/json",
      Accept: "text/event-stream",
    },
    data: { message, case_id: caseId, session_id: null },
    timeout: 60_000,
  });
  const body = await res.text();
  expect(
    res.status(),
    `chat failed: ${res.status()} ${body.slice(0, 400)}`,
  ).toBe(200);
  return body;
}

function sseEvents(body: string): Record<string, unknown>[] {
  const out: Record<string, unknown>[] = [];
  for (const line of body.split(/\r?\n/)) {
    if (!line.startsWith("data: ")) continue;
    const payload = line.slice("data: ".length);
    if (!payload || payload === "[DONE]") continue;
    try {
      const parsed: unknown = JSON.parse(payload);
      if (isRecord(parsed)) out.push(parsed);
    } catch {
      // Non-JSON keepalive / text frame — not an event we assert on.
    }
  }
  return out;
}

test("one runaway turn is capped in the wallet's own unit, not just in estimated input tokens", async ({
  request,
}) => {
  const caseId = await openCase(request);
  const body = await chatSse(
    request,
    caseId,
    "MOCK_WALLET_CAP_TRIP expensive turn that no input estimator can see",
  );
  const events = sseEvents(body);

  // 1. The MEASURED breaker is the one that fired.
  const metrics = events.find((e) => e.type === "tool_loop_metrics");
  expect(
    metrics,
    "tool_loop_metrics event must be present in the SSE stream",
  ).toBeTruthy();
  expect(
    String(metrics?.terminated_reason ?? ""),
    "expected wallet_cap_trip — end_turn means the breaker never fired " +
      "(the turn ran to the mock's hard cap); cumulative_cost_trip or " +
      "cost_circuit_trip would mean an input-token estimator somehow saw " +
      "a burn it cannot model, which would make this scenario a bad " +
      "regression guard rather than a passing one",
  ).toBe("wallet_cap_trip");

  // 2. The burn the wallet actually took is BOUNDED. This is the
  //    ticket's real assertion: the ceiling plus the one tools-stripped
  //    synthesis call the user is still owed. Allowing 3x the ceiling
  //    leaves generous room for that slack term while staying far below
  //    the ~1.2M an unbounded turn reaches at the mock's hard cap.
  const balance = events.find((e) => e.event === "token_balance");
  expect(
    balance,
    "token_balance event must be present — the UI decrements from it",
  ).toBeTruthy();

  const burned = Number(balance?.burned_tokens ?? 0);
  expect(
    burned,
    "the turn should have burned a lot (that is the scenario)",
  ).toBeGreaterThan(TOKENS_PER_TURN);
  expect(
    burned,
    `one turn burned ${burned} wallet tokens against a ${TURN_MAX_WALLET_TOKENS} ` +
      "ceiling — the per-turn wallet bound is not holding",
  ).toBeLessThan(3 * TURN_MAX_WALLET_TOKENS);

  // 3. And therefore the account is not locked out. This is the harm
  //    the ticket describes, stated directly.
  //
  //    Lockout is `balance <= CHAT_MIN_BALANCE` (the pre-flight floor,
  //    0), so a positive balance is the assertion. Deliberately NOT
  //    `approx_turns_remaining > 0`: that display figure floors to 0
  //    below one whole turn, and against the ABS-404 3-turn grant a
  //    worst-case runaway first question can legitimately land there
  //    with chat still working. Asserting on it would pin a display
  //    rounding rule while claiming to test a lockout.
  const balanceTokens = Number(balance?.balance_tokens ?? 0);
  expect(
    balanceTokens,
    `a brand-new wallet (grant ${SIGNUP_GRANT}) went to ${balanceTokens} ` +
      "after ONE question — the ABS-404 lockout has regressed",
  ).toBeGreaterThan(CHAT_MIN_BALANCE);
});
