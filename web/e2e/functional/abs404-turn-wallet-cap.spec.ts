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
  SIGNUP_GRANT,
  TOKENS_PER_TURN,
  TURN_MAX_WALLET_TOKENS,
} from "../fixtures/wallet-params";
import { resolveDatabaseUrl } from "../helpers/database-url";

const TEST_USER_ID = `abs404-${Date.now()}-${Math.random()
  .toString(36)
  .slice(2, 8)}`;

test.beforeAll(() => {
  const repoRoot = path.resolve(__dirname, "..", "..", "..");
  const seed = path.join(repoRoot, "scripts", "seed_e2e_user.py");
  const venvPython = path.join(repoRoot, ".venv", "bin", "python");
  const databaseUrl = resolveDatabaseUrl();
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

/** One SSE frame: the `event:` name and its parsed `data:` payload. */
type SseFrame = { name: string; data: Record<string, unknown> };

/**
 * Parse the raw stream into named frames.
 *
 * The name has to come off the `event:` line: `token_balance`'s payload
 * carries no self-describing field, so a parser that reads only `data:`
 * lines can never find it. (`tool_loop_metrics` happens to repeat its
 * name in a `type` key, which is why a data-only parser finds that one
 * and silently misses the other.)
 */
function sseFrames(body: string): SseFrame[] {
  const out: SseFrame[] = [];
  let name = "message";
  for (const line of body.split(/\r?\n/)) {
    if (line.startsWith("event:")) {
      name = line.slice("event:".length).trim();
      continue;
    }
    if (!line.startsWith("data: ")) {
      // A blank line closes the frame; the next one is unnamed until
      // its own `event:` line says otherwise.
      if (line.trim() === "") name = "message";
      continue;
    }
    const payload = line.slice("data: ".length);
    if (!payload || payload === "[DONE]") continue;
    try {
      const parsed: unknown = JSON.parse(payload);
      if (isRecord(parsed)) out.push({ name, data: parsed });
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
  const frames = sseFrames(body);

  // 1. The MEASURED breaker is the one that fired.
  const metrics = frames.find((f) => f.name === "tool_loop_metrics")?.data;
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
  const balance = frames.find((f) => f.name === "token_balance")?.data;
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

  // 3. And therefore a brand-new account is not locked out by its first
  //    question — the harm the ticket describes, stated directly.
  //
  //    Stated as burn-vs-grant, NOT as the balance on the wire: this
  //    spec's user is seeded by `seed_e2e_user.py`, which tops the
  //    wallet far above the signup grant, so its post-turn balance
  //    would stay comfortably positive even with the breaker deleted.
  //    Only comparing the burn against the grant a real new account
  //    actually gets makes this assertion load-bearing — an unbounded
  //    turn reaches ~1.2M at the mock's hard cap, more than double the
  //    grant, which is a first question that ends the account.
  //
  //    Deliberately NOT `approx_turns_remaining > 0` either: that
  //    display figure floors to 0 below one whole turn, and against the
  //    ABS-404 3-turn grant a worst-case runaway first question can
  //    legitimately land there with chat still working. Asserting on it
  //    would pin a display rounding rule while claiming to test a
  //    lockout.
  expect(
    burned,
    `one question burned ${burned} of a ${SIGNUP_GRANT}-token signup grant — ` +
      "a new account cannot survive its own first turn, which is the " +
      "ABS-404 lockout",
  ).toBeLessThan(SIGNUP_GRANT);
});
