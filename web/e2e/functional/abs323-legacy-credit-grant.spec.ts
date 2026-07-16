// Functional: ABS-323 — payments-off launch blocker.
//
// Before the tier→question pivot, the signup free-question grant was
// skipped for any user who already held a legacy CaseCredit row (the
// "holds credits ⇒ already onboarded" assumption). After the pivot those
// tier credits can no longer buy answers, so with payments off
// (ADVISOR_PAYMENTS_ENABLED=false) every existing user was left at
// free_questions_remaining = 0 with no way to get an answer — the
// case-open form showed "Free trial used — paid answers coming soon" and
// no action button.
//
// The fix makes ``free_question_grant_issued`` the sole idempotency gate
// for grant_starter_credits_if_needed and runs the self-heal on every
// authenticated request. A legacy-credit user therefore self-heals to
// the default grant the next time they authenticate — no backfill.
//
// This spec pins that self-heal at the API boundary that drives the
// case-open form's "Get answer (free trial)" button: a legacy-credit
// user seeded WITHOUT a free-question count (flag unset = pre-pivot
// account) hits GET /v1/billing/me and comes back with the full grant,
// while their legacy tier credits remain reported alongside it.

import { execSync } from "node:child_process";
import * as path from "node:path";

import { expect, test } from "@playwright/test";

import { E2E_API_URL } from "../fixtures/test-env";

// FREE_QUESTION_GRANT in src/advisor/db/cases.py.
const FREE_QUESTION_GRANT = 3;

function makeUserId(suffix: string): string {
  return `abs323-${suffix}-${Date.now()}-${Math.random()
    .toString(36)
    .slice(2, 8)}`;
}

const REPO_ROOT = path.resolve(__dirname, "..", "..", "..");
const VENV_PYTHON = path.join(REPO_ROOT, ".venv", "bin", "python");

function pyEnv(): NodeJS.ProcessEnv {
  const pgPort = process.env.PG_PORT || "5432";
  const databaseUrl =
    process.env.DATABASE_URL ||
    `postgresql+psycopg://layer1:layer1@localhost:${pgPort}/layer1_test`;
  return {
    ...process.env,
    DATABASE_URL: databaseUrl,
    PYTHONPATH: `${path.join(REPO_ROOT, "src")}:${process.env.PYTHONPATH || ""}`,
  };
}

/**
 * Seed a *legacy* account: holds tier credits but is NOT seeded with a
 * free-question count, so ``free_question_grant_issued`` stays unset —
 * exactly the pre-pivot user the bug locked out. Passing --free-questions
 * would set the grant flag and suppress the self-heal we want to observe.
 */
function seedLegacyCreditUser(opts: {
  userId: string;
  creditsPerTier: number;
}): void {
  const seed = path.join(REPO_ROOT, "scripts", "seed_e2e_user.py");
  execSync(
    `"${VENV_PYTHON}" "${seed}" ` +
      `--user-id "${opts.userId}" ` +
      `--email "${opts.userId}@e2e.test" ` +
      `--credits-per-tier ${opts.creditsPerTier}`,
    { env: pyEnv(), stdio: "inherit" },
  );
}

type BillingMe = {
  free_questions_remaining: number;
  tier_balances: Array<{ tier: string; available: number }>;
  total_available_credits: number;
};

async function billingMe(
  request: import("@playwright/test").APIRequestContext,
  userId: string,
): Promise<BillingMe> {
  const res = await request.get(`${E2E_API_URL}/v1/billing/me`, {
    headers: { "X-Test-User-Id": userId },
  });
  expect(
    res.status(),
    `expected 200 but got ${res.status()}: ${await res.text()}`,
  ).toBe(200);
  return (await res.json()) as BillingMe;
}

test("legacy-credit user self-heals to the free-question grant on next request", async ({
  request,
}) => {
  const userId = makeUserId("legacy");
  // Holds 2 credits in each tier (quick/standard/complex), no free-question
  // grant flag — the locked-out pre-pivot shape verified on dev.
  seedLegacyCreditUser({ userId, creditsPerTier: 2 });

  // First authenticated request resolves the user → the ABS-323 self-heal
  // issues the free-question grant.
  const body = await billingMe(request, userId);

  expect(
    body.free_questions_remaining,
    "legacy-credit user should self-heal to the full free-question grant",
  ).toBe(FREE_QUESTION_GRANT);

  // Legacy tier credits coexist with the grant — they are not consumed or
  // cleared by the self-heal.
  const standardBal = body.tier_balances.find((b) => b.tier === "standard");
  expect(
    standardBal?.available,
    "legacy standard credits remain reported alongside the new grant",
  ).toBe(2);
});

test("self-heal is idempotent — a consumed question is not topped back up on next login", async ({
  request,
}) => {
  const userId = makeUserId("idempotent");
  seedLegacyCreditUser({ userId, creditsPerTier: 1 });

  // First login grants the full pack.
  const first = await billingMe(request, userId);
  expect(first.free_questions_remaining).toBe(FREE_QUESTION_GRANT);

  // Spend one free question (consumes the entitlement).
  // ABS-324: free-start now opens an Answers QuestionPurchase and validates
  // the question's required inputs BEFORE reserving the credit (the
  // failed-question rule). `permitted_use` requires both `address` and
  // `proposed_use`, so the call must supply complete inputs or it 400s
  // ("missing_required_inputs") instead of consuming the free question.
  const start = await request.post(
    `${E2E_API_URL}/v1/billing/questions/free-start`,
    {
      headers: { "X-Test-User-Id": userId },
      data: {
        question_slug: "permitted_use",
        inputs: {
          address: "123 Test St, Halifax",
          proposed_use: "a four-unit dwelling",
        },
        anchor_label: "123 Test St, Halifax",
        anchor_kind: "address",
      },
    },
  );
  expect(start.status(), await start.text()).toBe(200);
  expect((await start.json()).free_questions_remaining).toBe(
    FREE_QUESTION_GRANT - 1,
  );

  // A subsequent login must NOT re-grant: the flag is the sole gate, so
  // the consumed question stays consumed (the bug would have been a
  // counter reset to the full grant on every request).
  const second = await billingMe(request, userId);
  expect(
    second.free_questions_remaining,
    "the grant issues once; repeated logins do not restore consumed questions",
  ).toBe(FREE_QUESTION_GRANT - 1);
});
