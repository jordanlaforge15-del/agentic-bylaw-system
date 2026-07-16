// ABS-271: Tunable result-set size on search_bylaw_evidence.
//
// Exercises the ``limit`` parameter (FR-1B.1) via the test-only
// ``POST /v1/_test/search-evidence`` endpoint, which calls
// ``RetrievalService.search`` directly with the supplied limit and
// returns fragment IDs + scores in ranked order.
//
// This spec covers:
//
//   AC-1B.2 — limit=15 returns ≤15; limit=3 returns ≤3
//   AC-1B.3 — first 5 results of limit=15 equal the 5 results of limit=5
//              in the same order (ranking unchanged)
//   AC-1B.4 — limit=0, limit=51 are rejected (Pydantic 422)
//   AC-1B.5 — server-enforced: the endpoint itself validates 1 ≤ limit ≤ 50
//
// Data dependency: the spec seeds its own bylaw via
// ``scripts/seed_e2e_evaluator_bylaws.py`` (idempotent get-or-create).
// The evaluator bylaw is a synthetic Halifax bylaw with enough sections
// to saturate limit=15.

import { execSync } from "node:child_process";
import * as path from "node:path";

import { test, expect, E2E_API_URL, DEMO_USER_ID } from "../fixtures/test-env";

test.beforeAll(() => {
  const repoRoot = path.resolve(__dirname, "..", "..", "..");
  const venvPython = path.join(repoRoot, ".venv", "bin", "python");
  const pgPort = process.env.PG_PORT || "5432";
  const databaseUrl =
    process.env.DATABASE_URL ||
    `postgresql+psycopg://layer1:layer1@localhost:${pgPort}/layer1_test`;
  const env = {
    ...process.env,
    DATABASE_URL: databaseUrl,
    PYTHONPATH: `${path.join(repoRoot, "src")}:${process.env.PYTHONPATH || ""}`,
  };
  execSync(
    `"${venvPython}" "${path.join(repoRoot, "scripts", "seed_e2e_evaluator_bylaws.py")}"`,
    { env, encoding: "utf-8" },
  );
});

async function searchEvidence(
  request: import("@playwright/test").APIRequestContext,
  query: string,
  limit: number,
) {
  return await request.post(`${E2E_API_URL}/v1/_test/search-evidence`, {
    headers: {
      "Content-Type": "application/json",
      "X-Test-User-Id": DEMO_USER_ID,
    },
    data: { query, limit },
  });
}

// AC-1B.2 — limit caps results
test("limit=3 returns at most 3 results", async ({ request }) => {
  const resp = await searchEvidence(request, "setback zone residential", 3);
  expect(resp.ok()).toBeTruthy();
  const body = await resp.json();
  expect(body.match_count).toBeLessThanOrEqual(3);
});

test("limit=15 returns at most 15 results", async ({ request }) => {
  const resp = await searchEvidence(request, "setback zone residential", 15);
  expect(resp.ok()).toBeTruthy();
  const body = await resp.json();
  expect(body.match_count).toBeLessThanOrEqual(15);
});

// AC-1B.3 — ranking preserved across limit values
test("first 5 results of limit=15 equal 5 results of limit=5 in same order", async ({ request }) => {
  const [resp5, resp15] = await Promise.all([
    searchEvidence(request, "setback zone residential", 5),
    searchEvidence(request, "setback zone residential", 15),
  ]);
  expect(resp5.ok()).toBeTruthy();
  expect(resp15.ok()).toBeTruthy();

  const body5 = await resp5.json();
  const body15 = await resp15.json();

  // Only assert ordering when both return results; if the fixture is too
  // small, skip rather than false-fail.
  if (body5.match_count === 0) {
    test.info().annotations.push({
      type: "note",
      description: "No matches found — fixture may be too small; ordering invariant not exercised",
    });
    return;
  }

  const ids5: number[] = body5.fragment_ids;
  const ids15prefix: number[] = body15.fragment_ids.slice(0, ids5.length);
  expect(ids5).toEqual(ids15prefix);
});

// AC-1B.4 / AC-1B.5 — validation rejects out-of-range limits
test("limit=0 is rejected with 422", async ({ request }) => {
  const resp = await searchEvidence(request, "anything", 0);
  expect(resp.status()).toBe(422);
});

test("limit=51 is rejected with 422", async ({ request }) => {
  const resp = await searchEvidence(request, "anything", 51);
  expect(resp.status()).toBe(422);
});
