// Functional: ABS-382 — free case open + tier-upgrade retirement.
//
// A signed-in user with NO purchases (zero CaseCredits, zero tokens)
// opens a case through the Next.js proxy (POST /api/cases). The open
// must:
//   * succeed with 200 (never 402 no_available_credit — the case-open
//     credit gate is retired),
//   * reserve no credit (`credit_id` null) and carry no tier
//     (`case.current_tier` null),
//   * be reused (not duplicated) for the same anchor within the window,
//   * ignore a legacy `tier` in the body (no 422),
//   * be usable afterwards — it shows up in GET /api/cases.
// And the retired upgrade endpoint returns 410 tier_model_retired via
// its proxy.
//
// The spec drives everything through the proxy authenticated AS a
// freshly-seeded zero-purchase user (its own browser context + minted
// cookies), so the "no purchases" precondition is real rather than
// inherited from the shared demo user.

import { execSync } from "node:child_process";
import * as path from "node:path";

import { expect, test, type BrowserContext } from "@playwright/test";

import { E2E_API_URL, E2E_BASE_URL } from "../fixtures/test-env";
import { resolveDatabaseUrl } from "../helpers/database-url";

const NO_PURCHASE_USER = `free-open-${Date.now()}-${Math.random()
  .toString(36)
  .slice(2, 8)}`;

test.beforeAll(() => {
  const repoRoot = path.resolve(__dirname, "..", "..", "..");
  const seed = path.join(repoRoot, "scripts", "seed_e2e_user.py");
  const venvPython = path.join(repoRoot, ".venv", "bin", "python");
  const databaseUrl = resolveDatabaseUrl();
  const seedEnv = {
    ...process.env,
    DATABASE_URL: databaseUrl,
    PYTHONPATH: `${path.join(repoRoot, "src")}:${
      process.env.PYTHONPATH || ""
    }`,
  };
  // Zero credits at every tier, no unlimited flag — a true no-purchase user.
  execSync(
    `"${venvPython}" "${seed}" --user-id "${NO_PURCHASE_USER}" ` +
      `--email "${NO_PURCHASE_USER}@e2e.test" --credits-per-tier 0`,
    { env: seedEnv, stdio: "inherit" },
  );
});

// Mint a test JWT + identity cookies so requests through the Next
// proxy resolve to NO_PURCHASE_USER (mirrors the shared fixture, but
// for our dedicated user rather than the demo user).
async function authAsNoPurchaseUser(
  context: BrowserContext,
): Promise<void> {
  const target = E2E_BASE_URL;
  const jwtRes = await context.request.post(
    `${E2E_API_URL}/v1/_test/mint-jwt`,
    { data: { sub: NO_PURCHASE_USER, email: `${NO_PURCHASE_USER}@e2e.test` } },
  );
  expect(jwtRes.ok(), await jwtRes.text()).toBeTruthy();
  const { token } = (await jwtRes.json()) as { token: string };
  const host = new URL(target).hostname;
  await context.addCookies([
    {
      name: "abs_test_sub_user_id",
      value: NO_PURCHASE_USER,
      domain: host,
      path: "/",
      sameSite: "Lax",
    },
    {
      name: "abs_test_clerk_jwt",
      value: token,
      domain: host,
      path: "/",
      sameSite: "Lax",
    },
  ]);
}

test("no-purchase user opens a case via the proxy — free, no 402, usable", async ({
  browser,
}) => {
  const context = await browser.newContext();
  await authAsNoPurchaseUser(context);
  const req = context.request;

  const anchorLabel = `free-open-addr-${Date.now()}`;

  // Open via the Next proxy. No credit, no tier in the body.
  const openRes = await req.post(`${E2E_BASE_URL}/api/cases`, {
    data: { anchor_label: anchorLabel, anchor_kind: "address" },
  });
  expect(
    openRes.status(),
    `expected 200 but got ${openRes.status()}: ${await openRes.text()}`,
  ).toBe(200);
  const opened = (await openRes.json()) as {
    case: { id: number; current_tier: string | null };
    credit_id: number | null;
    reused_existing_case: boolean;
  };
  expect(opened.credit_id, "no credit reserved on a free open").toBeNull();
  expect(opened.case.current_tier, "free case carries no tier").toBeNull();
  expect(opened.reused_existing_case).toBe(false);
  const caseId = opened.case.id;

  // Same anchor within the window → reused, not duplicated.
  const reopenRes = await req.post(`${E2E_BASE_URL}/api/cases`, {
    data: { anchor_label: anchorLabel, anchor_kind: "address" },
  });
  expect(reopenRes.status()).toBe(200);
  const reopened = (await reopenRes.json()) as {
    case: { id: number };
    reused_existing_case: boolean;
  };
  expect(reopened.reused_existing_case).toBe(true);
  expect(reopened.case.id).toBe(caseId);

  // A legacy `tier` in the body is accepted-but-ignored (no 422).
  const legacyRes = await req.post(`${E2E_BASE_URL}/api/cases`, {
    data: {
      anchor_label: `free-open-legacy-${Date.now()}`,
      anchor_kind: "address",
      tier: "complex",
    },
  });
  expect(
    legacyRes.status(),
    `legacy tier body should be accepted: ${await legacyRes.text()}`,
  ).toBe(200);
  const legacy = (await legacyRes.json()) as {
    credit_id: number | null;
    case: { current_tier: string | null };
  };
  expect(legacy.credit_id).toBeNull();
  expect(legacy.case.current_tier).toBeNull();

  // Case is usable: it lists in GET /api/cases.
  const listRes = await req.get(`${E2E_BASE_URL}/api/cases`);
  expect(listRes.status()).toBe(200);
  const list = (await listRes.json()) as {
    cases: Array<{ id: number; anchor_label: string }>;
  };
  expect(list.cases.some((c) => c.id === caseId)).toBe(true);

  // The tier-upgrade endpoint is retired: 410 tier_model_retired.
  const upgradeRes = await req.post(
    `${E2E_BASE_URL}/api/cases/${caseId}/upgrade`,
    { data: { target_tier: "complex" } },
  );
  expect(upgradeRes.status()).toBe(410);
  const upgrade = (await upgradeRes.json()) as {
    detail: { code: string };
  };
  expect(upgrade.detail.code).toBe("tier_model_retired");

  await context.close();
});
