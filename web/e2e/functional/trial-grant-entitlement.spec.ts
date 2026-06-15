// Functional: ABS-314 — trial grant rework.
//
// The signup trial grant was previously '3 standard CaseCredit rows'.
// It is now a free-question entitlement counter: new users receive
// FREE_QUESTION_GRANT (3) free questions that are consumed before any
// Stripe charge is placed.
//
// This spec verifies:
//   1. A freshly-seeded user with --free-questions 3 sees
//      free_questions_remaining == 3 in GET /v1/billing/me.
//   2. A user seeded with --free-questions 0 sees 0 (simulates a user
//      who has consumed all free questions or was created pre-feature).
//   3. Legacy CaseCredit tier_balances are still reported correctly
//      alongside the new counter (grandfathering path).
//
// All three users are unique per run so they don't race the shared demo
// user's credit pool. The spec calls FastAPI directly — no browser
// rendering needed since the counter lives in the API response.

import { execSync } from "node:child_process";
import * as path from "node:path";

import { expect, test } from "@playwright/test";

import { E2E_API_URL } from "../fixtures/test-env";

function makeUserId(suffix: string): string {
  return `abs314-${suffix}-${Date.now()}-${Math.random()
    .toString(36)
    .slice(2, 8)}`;
}

function seedUser(opts: {
  userId: string;
  freeQuestions: number;
  creditsPerTier?: number;
}): void {
  const repoRoot = path.resolve(__dirname, "..", "..", "..");
  const seed = path.join(repoRoot, "scripts", "seed_e2e_user.py");
  const venvPython = path.join(repoRoot, ".venv", "bin", "python");
  const pgPort = process.env.PG_PORT || "5432";
  const databaseUrl =
    process.env.DATABASE_URL ||
    `postgresql+psycopg://layer1:layer1@localhost:${pgPort}/layer1_test`;

  execSync(
    `"${venvPython}" "${seed}" ` +
      `--user-id "${opts.userId}" ` +
      `--email "${opts.userId}@e2e.test" ` +
      `--credits-per-tier ${opts.creditsPerTier ?? 0} ` +
      `--free-questions ${opts.freeQuestions}`,
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

const FRESH_USER = makeUserId("fresh");
const ZERO_USER = makeUserId("zero");
const LEGACY_USER = makeUserId("legacy");

test.beforeAll(() => {
  // Fresh user: 3 free questions, no legacy credits.
  seedUser({ userId: FRESH_USER, freeQuestions: 3, creditsPerTier: 0 });
  // Zero user: 0 free questions (consumed all, or pre-feature).
  seedUser({ userId: ZERO_USER, freeQuestions: 0, creditsPerTier: 0 });
  // Legacy user: 0 free questions + 2 standard legacy credits (grandfathered).
  seedUser({ userId: LEGACY_USER, freeQuestions: 0, creditsPerTier: 0 });

  // Grant legacy credits to LEGACY_USER via the tier-seed script.
  const repoRoot = path.resolve(__dirname, "..", "..", "..");
  const seedTier = path.join(
    repoRoot,
    "scripts",
    "seed_e2e_tier_credits.py",
  );
  const venvPython = path.join(repoRoot, ".venv", "bin", "python");
  const pgPort = process.env.PG_PORT || "5432";
  const databaseUrl =
    process.env.DATABASE_URL ||
    `postgresql+psycopg://layer1:layer1@localhost:${pgPort}/layer1_test`;

  execSync(
    `"${venvPython}" "${seedTier}" ` +
      `--user-id "${LEGACY_USER}" ` +
      `--tier standard --quantity 2`,
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

test("fresh user: free_questions_remaining == 3", async ({ request }) => {
  const res = await request.get(`${E2E_API_URL}/v1/billing/me`, {
    headers: { "X-Test-User-Id": FRESH_USER },
  });
  expect(
    res.status(),
    `expected 200 but got ${res.status()}: ${await res.text()}`,
  ).toBe(200);

  const body = (await res.json()) as {
    free_questions_remaining: number;
    tier_balances: Array<{ tier: string; available: number }>;
  };

  expect(
    body.free_questions_remaining,
    "fresh user should have 3 free questions remaining",
  ).toBe(3);

  // Legacy credit balances should be zero for a credit-free user.
  const standardBal = body.tier_balances.find((b) => b.tier === "standard");
  expect(
    standardBal?.available ?? 0,
    "fresh user should have 0 standard credits",
  ).toBe(0);
});

test("zero user: free_questions_remaining == 0", async ({ request }) => {
  const res = await request.get(`${E2E_API_URL}/v1/billing/me`, {
    headers: { "X-Test-User-Id": ZERO_USER },
  });
  expect(res.status()).toBe(200);

  const body = (await res.json()) as { free_questions_remaining: number };
  expect(
    body.free_questions_remaining,
    "user with no grant should have 0 free questions",
  ).toBe(0);
});

test("legacy user: free_questions_remaining == 0 + standard credits still show", async ({
  request,
}) => {
  const res = await request.get(`${E2E_API_URL}/v1/billing/me`, {
    headers: { "X-Test-User-Id": LEGACY_USER },
  });
  expect(res.status()).toBe(200);

  const body = (await res.json()) as {
    free_questions_remaining: number;
    tier_balances: Array<{ tier: string; available: number }>;
    total_available_credits: number;
  };

  expect(
    body.free_questions_remaining,
    "legacy user has no free-question grant",
  ).toBe(0);

  const standardBal = body.tier_balances.find((b) => b.tier === "standard");
  expect(
    standardBal?.available,
    "legacy standard credits should still be reported (grandfathered)",
  ).toBe(2);

  expect(
    body.total_available_credits,
    "total_available_credits reflects legacy credits",
  ).toBe(2);
});
