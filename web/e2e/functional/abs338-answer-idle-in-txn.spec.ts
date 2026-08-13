// Functional: ABS-338 — a slow answer turn must not 500 on Postgres'
// idle-in-transaction timeout.
//
// Reported symptom (2026-06-25, 6184 Quinpool Road): submitting a priced
// question against the real Postgres-backed stack returned HTTP 500 after
// ~84s.
//
// Root cause: the answer request held ONE Postgres transaction open across
// the entire LLM turn. The request SELECTs opened it, the tool loop ran with
// it idle, and the settling UPDATE was the next statement on it. Postgres'
// idle_in_transaction_session_timeout (60s in dev/e2e — docker-compose.yml,
// ABS-100) measures exactly that gap, terminated the connection mid-turn, and
// the settling db.flush() raised
// psycopg.errors.IdleInTransactionSessionTimeout -> 500 on a grounded,
// paid-for answer. NOT the ABS-332 NUL bug: different exception class.
//
// Waiting out a real 60s cap in an e2e is absurd, so the /v1/_test/buy-answer/
// answer-slow-turn harness shrinks the SAME mechanism: it pins a 1s
// idle_in_transaction_session_timeout on the request connection (a dedicated
// NullPool engine, so the cap never leaks into the pool) and injects a 2.5s
// LLM turn — 2.5x the cap. Before the fix that combination kills the
// connection mid-turn; after it, run_answer / run_refinement hold no
// transaction while the turn runs, so there is nothing to kill.
//
// The deterministic, backend-agnostic invariant ("the request session holds
// no open transaction during the turn") lives in
// tests/advisor/billing/test_answer_no_open_txn_across_llm.py.

import { execSync } from "node:child_process";
import * as path from "node:path";

import { E2E_API_URL, expect, test } from "../fixtures/test-env";
import { resolveDatabaseUrl } from "../helpers/database-url";

type Req = import("@playwright/test").APIRequestContext;

const INPUTS = {
  address: "6184 Quinpool Road, Halifax",
  proposed_use: "a law office",
};

// Two slow turns (answer + refinement) at 2.5s each, plus stack overhead.
const TEST_TIMEOUT_MS = 90_000;
const REQUEST_TIMEOUT_MS = 60_000;

function makeUserId(suffix: string): string {
  return `abs338-${suffix}-${Date.now()}-${Math.random()
    .toString(36)
    .slice(2, 8)}`;
}

function seedUser(userId: string, freeQuestions: number): void {
  const repoRoot = path.resolve(__dirname, "..", "..", "..");
  const seed = path.join(repoRoot, "scripts", "seed_e2e_user.py");
  const venvPython = path.join(repoRoot, ".venv", "bin", "python");
  const databaseUrl = resolveDatabaseUrl();
  execSync(
    `"${venvPython}" "${seed}" ` +
      `--user-id "${userId}" ` +
      `--email "${userId}@e2e.test" ` +
      `--credits-per-tier 0 ` +
      `--free-questions ${freeQuestions}`,
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
}

async function freeStart(request: Req, userId: string) {
  return request.post(`${E2E_API_URL}/v1/billing/questions/free-start`, {
    headers: { "X-Test-User-Id": userId },
    data: {
      question_slug: "permitted_use",
      inputs: INPUTS,
      anchor_label: INPUTS.address,
      anchor_kind: "address",
    },
  });
}

test("a slow answer turn survives the idle-in-transaction cap (real Postgres)", async ({
  request,
}) => {
  test.setTimeout(TEST_TIMEOUT_MS);

  const userId = makeUserId("answer");
  seedUser(userId, 1);

  const startRes = await freeStart(request, userId);
  expect(startRes.status(), await startRes.text()).toBe(200);
  const { purchase_id } = (await startRes.json()) as { purchase_id: number };

  // A 2.5s turn under a 1s idle cap. A 500 here is the ABS-338 regression:
  // the connection was killed mid-turn and the settling UPDATE ran on a dead
  // session. The refine leg proves the sibling flaw in run_refinement.
  const res = await request.post(
    `${E2E_API_URL}/v1/_test/buy-answer/answer-slow-turn`,
    {
      data: {
        purchase_id,
        idle_cap_ms: 1000,
        turn_delay_s: 2.5,
        refine_message: "Please summarize the answer in three bullet points.",
      },
      timeout: REQUEST_TIMEOUT_MS,
    },
  );
  expect(res.status(), await res.text()).toBe(200);

  const body = (await res.json()) as {
    status: string;
    answer: string | null;
    refined_answer: string | null;
    refinement_count: number;
  };
  // Delivery did not regress: the answer grounded, captured, and persisted.
  expect(body.status).toBe("captured");
  expect(body.answer).toBeTruthy();
  // The refinement turn settled too — same phasing, same connection cap.
  expect(body.refined_answer).toBeTruthy();
  expect(body.refinement_count).toBe(1);
});
