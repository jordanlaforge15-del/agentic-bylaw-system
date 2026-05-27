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

test("standard case opens when user has standard credits", async ({
  request,
}) => {
  const res = await request.post(`${E2E_API_URL}/v1/cases`, {
    headers: { "X-Test-User-Id": STANDARD_ONLY_USER },
    data: {
      anchor_label: `std-consistency-standard-${Date.now()}`,
      anchor_kind: "address",
      tier: "standard",
    },
  });
  expect(
    res.status(),
    `expected 200 but got ${res.status()}: ${await res.text()}`,
  ).toBe(200);
});

test("quick lookup is blocked when user has no quick credits", async ({
  request,
}) => {
  const res = await request.post(`${E2E_API_URL}/v1/cases`, {
    headers: { "X-Test-User-Id": STANDARD_ONLY_USER },
    data: {
      anchor_label: `std-consistency-quick-${Date.now()}`,
      anchor_kind: "address",
      tier: "quick",
    },
  });
  expect(res.status()).toBe(402);
  const body = (await res.json()) as {
    detail: { code: string; tier: string };
  };
  expect(body.detail.code).toBe("no_available_credit");
  expect(body.detail.tier).toBe("quick");
});

test("complex file is blocked when user has no complex credits", async ({
  request,
}) => {
  const res = await request.post(`${E2E_API_URL}/v1/cases`, {
    headers: { "X-Test-User-Id": STANDARD_ONLY_USER },
    data: {
      anchor_label: `std-consistency-complex-${Date.now()}`,
      anchor_kind: "address",
      tier: "complex",
    },
  });
  expect(res.status()).toBe(402);
  const body = (await res.json()) as {
    detail: { code: string; tier: string };
  };
  expect(body.detail.code).toBe("no_available_credit");
  expect(body.detail.tier).toBe("complex");
});
