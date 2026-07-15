// Functional: ABS-183 credit-gating consistency across tiers.
//
// Reproduces the bug where a user with only standard credits could open
// a Standard Case freely while Quick Lookup was hard-blocked — and the
// billing page showed "0" for every tier because GET /billing/me
// returned 503 in dormant mode.
//
// After the fix:
// * GET /v1/billing/me returns 200 with real per-tier balances even
//   when ADVISOR_BILLING_ENABLED=false (dormant mode).
// * A user with 3 standard credits (the signup starter grant) can open
//   a Standard Case (200) but is correctly blocked on Quick Lookup
//   (402) — and those blocks are accurately reflected in /billing/me.
//
// Uses a dedicated per-run user so it doesn't touch the shared demo
// user's credit pool.

import { execSync } from "node:child_process";
import * as path from "node:path";

import { expect, test } from "@playwright/test";

import { E2E_API_URL } from "../fixtures/test-env";

const STANDARD_ONLY_USER = `std-only-${Date.now()}-${Math.random()
  .toString(36)
  .slice(2, 8)}`;

test.beforeAll(() => {
  const repoRoot = path.resolve(__dirname, "..", "..", "..");
  const seedUser = path.join(repoRoot, "scripts", "seed_e2e_user.py");
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

  const seedEnv = {
    ...process.env,
    DATABASE_URL: databaseUrl,
    PYTHONPATH: `${path.join(repoRoot, "src")}:${
      process.env.PYTHONPATH || ""
    }`,
  };

  // Create user with 0 credits at every tier (simulates a new user
  // before any starter grant is applied manually by an admin).
  execSync(
    `"${venvPython}" "${seedUser}" --user-id "${STANDARD_ONLY_USER}" ` +
      `--email "${STANDARD_ONLY_USER}@e2e.test" --credits-per-tier 0`,
    { env: seedEnv, stdio: "inherit" },
  );

  // Grant 3 standard credits only — this mirrors the signup starter
  // grant (STARTER_GRANT_TIER="standard", STARTER_GRANT_QUANTITY=3)
  // that every real beta user receives.
  execSync(
    `"${venvPython}" "${seedTier}" --user-id "${STANDARD_ONLY_USER}" ` +
      `--tier standard --quantity 3`,
    { env: seedEnv, stdio: "inherit" },
  );
});

test("GET /billing/me returns 200 with real balances in dormant mode", async ({
  request,
}) => {
  const res = await request.get(`${E2E_API_URL}/v1/billing/me`, {
    headers: { "X-Test-User-Id": STANDARD_ONLY_USER },
  });
  expect(
    res.status(),
    `expected 200 but got ${res.status()}: ${await res.text()}`,
  ).toBe(200);

  const body = (await res.json()) as {
    enabled: boolean;
    tier_balances: Array<{ tier: string; available: number }>;
    total_available_credits: number;
  };

  expect(body.enabled).toBe(false);

  const standardBal = body.tier_balances.find((b) => b.tier === "standard");
  expect(
    standardBal?.available,
    "standard tier should show 3 available credits",
  ).toBe(3);

  const quickBal = body.tier_balances.find((b) => b.tier === "quick");
  expect(
    quickBal?.available,
    "quick tier should show 0 available credits",
  ).toBe(0);

  expect(
    body.total_available_credits,
    "total should equal the standard balance",
  ).toBe(3);
});

// ABS-382: opening a case is now free regardless of tier credits — the
// case-open credit gate is retired. A tier in the body is accepted but
// ignored; the case opens with `current_tier=null` and `credit_id=null`
// no matter which tier (or none) the caller sends. The three tests below
// used to assert 402 on quick/complex; they now assert free open. The
// /billing/me balance display above is untouched by ABS-382.
test("case opens free even when the (legacy) tier has no credits", async ({
  request,
}) => {
  for (const tier of ["standard", "quick", "complex"]) {
    const res = await request.post(`${E2E_API_URL}/v1/cases`, {
      headers: { "X-Test-User-Id": STANDARD_ONLY_USER },
      data: {
        anchor_label: `std-consistency-${tier}-${Date.now()}`,
        anchor_kind: "address",
        tier,
      },
    });
    expect(
      res.status(),
      `tier=${tier}: expected 200 but got ${res.status()}: ${await res.text()}`,
    ).toBe(200);
    const body = (await res.json()) as {
      credit_id: number | null;
      case: { current_tier: string | null };
    };
    expect(body.credit_id, `tier=${tier}: no credit reserved`).toBeNull();
    expect(
      body.case.current_tier,
      `tier=${tier}: free case carries no tier`,
    ).toBeNull();
  }
});
