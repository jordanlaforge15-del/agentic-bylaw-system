// Functional: ABS-12 unlimited-credits mode for test users.
//
// Verifies that a user with `unlimited_credits=True` can open cases
// and chat without pre-granted credits, while a normal user (zero
// credits) still gets 402. Uses a dedicated per-run user id so it
// doesn't interfere with the shared demo user's credit pool.

import { execSync } from "node:child_process";
import * as path from "node:path";

import { expect, test } from "@playwright/test";

import { E2E_API_URL } from "../fixtures/test-env";

const UNLIMITED_USER = `unlimited-${Date.now()}-${Math.random()
  .toString(36)
  .slice(2, 8)}`;

const NO_CREDITS_USER = `nocred-${Date.now()}-${Math.random()
  .toString(36)
  .slice(2, 8)}`;

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
    PYTHONPATH: `${path.join(repoRoot, "src")}:${
      process.env.PYTHONPATH || ""
    }`,
  };

  // Unlimited user: zero pre-granted credits, unlimited_credits=True.
  execSync(
    `"${venvPython}" "${seed}" --user-id "${UNLIMITED_USER}" ` +
      `--email "${UNLIMITED_USER}@e2e.test" --credits-per-tier 0 --unlimited`,
    { env: seedEnv, stdio: "inherit" },
  );

  // Normal user: zero credits, unlimited_credits=False (default).
  execSync(
    `"${venvPython}" "${seed}" --user-id "${NO_CREDITS_USER}" ` +
      `--email "${NO_CREDITS_USER}@e2e.test" --credits-per-tier 0`,
    { env: seedEnv, stdio: "inherit" },
  );
});

test("unlimited-credits user can open a case without pre-granted credits", async ({
  request,
}) => {
  const res = await request.post(`${E2E_API_URL}/v1/cases`, {
    headers: { "X-Test-User-Id": UNLIMITED_USER },
    data: {
      anchor_label: "unlimited-test-addr-1",
      anchor_kind: "address",
      tier: "standard",
    },
  });
  expect(
    res.status(),
    `expected 200 but got ${res.status()}: ${await res.text()}`,
  ).toBe(200);
  const body = (await res.json()) as { credit_id: number };
  expect(body.credit_id).toBeGreaterThan(0);
});

test("unlimited-credits user can open multiple cases consecutively", async ({
  request,
}) => {
  for (let i = 0; i < 3; i++) {
    const res = await request.post(`${E2E_API_URL}/v1/cases`, {
      headers: { "X-Test-User-Id": UNLIMITED_USER },
      data: {
        anchor_label: `unlimited-multi-${i}-${Date.now()}`,
        anchor_kind: "address",
        tier: "standard",
      },
    });
    expect(
      res.status(),
      `case ${i} failed: ${res.status()} ${await res.text()}`,
    ).toBe(200);
  }
});

test("normal user with zero credits gets 402", async ({ request }) => {
  const res = await request.post(`${E2E_API_URL}/v1/cases`, {
    headers: { "X-Test-User-Id": NO_CREDITS_USER },
    data: {
      anchor_label: "no-credits-test",
      anchor_kind: "address",
      tier: "standard",
    },
  });
  expect(res.status()).toBe(402);
  const body = (await res.json()) as { code: string };
  expect(body.code).toBe("no_available_credit");
});

test("unlimited-credits user can chat after opening a case", async ({
  request,
}) => {
  const openRes = await request.post(`${E2E_API_URL}/v1/cases`, {
    headers: { "X-Test-User-Id": UNLIMITED_USER },
    data: {
      anchor_label: `unlimited-chat-${Date.now()}`,
      anchor_kind: "address",
      tier: "standard",
    },
  });
  expect(openRes.status()).toBe(200);
  const openBody = (await openRes.json()) as { case: { id: number } };
  const caseId = openBody.case.id;

  const chatRes = await request.post(`${E2E_API_URL}/v1/chat`, {
    headers: {
      "X-Test-User-Id": UNLIMITED_USER,
      "Content-Type": "application/json",
      Accept: "text/event-stream",
    },
    data: {
      message: "What is the minimum front yard setback?",
      case_id: caseId,
      session_id: null,
    },
    timeout: 15_000,
  });

  const chatBody = await chatRes.text();
  expect(
    chatRes.status(),
    `chat failed: ${chatRes.status()} ${chatBody.slice(0, 400)}`,
  ).toBe(200);
  expect(chatBody).toMatch(/text_delta/);
});
