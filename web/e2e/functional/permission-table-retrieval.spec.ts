// Functional: ABS-163 — Permission tables are found by the retrieval layer
// when they carry proper captions (e.g. "Table 1A: Permitted uses by zone").
//
// Seeds two SourceTable rows (Table 1A, Table 1B) via the e2e seed script,
// then hits the /v1/_test/search-tables endpoint to confirm the retrieval
// layer's _structured_permission_table_candidates() finds them by caption
// pattern and returns the correct cells for a given use + zone query.

import { execSync } from "node:child_process";
import * as path from "node:path";

import { E2E_API_URL, expect, test } from "../fixtures/test-env";


function runSeeds(): void {
  const repoRoot = path.resolve(__dirname, "..", "..", "..");
  const venvPython = path.join(repoRoot, ".venv", "bin", "python");
  const databaseUrl =
    process.env.DATABASE_URL ||
    "postgresql+psycopg://layer1:layer1@localhost:5432/layer1_test";
  const env = {
    ...process.env,
    DATABASE_URL: databaseUrl,
    PYTHONPATH: `${path.join(repoRoot, "src")}:${process.env.PYTHONPATH || ""}`,
  };
  execSync(
    `"${venvPython}" "${path.join(repoRoot, "scripts", "seed_e2e_permission_tables.py")}"`,
    { env, stdio: "inherit" },
  );
}


test.beforeAll(() => {
  runSeeds();
});


test("retrieval finds permission tables by caption", async ({ request }) => {
  const response = await request.post(`${E2E_API_URL}/v1/_test/search-tables`, {
    headers: { "Content-Type": "application/json" },
    data: {
      bylaw_name: "Regional Centre Land Use By-law",
      use_name: "Restaurant use",
    },
  });
  expect(response.status(), `search-tables failed: ${await response.text()}`).toBe(200);

  const body = await response.json();
  expect(body.table_count).toBeGreaterThanOrEqual(2);
  expect(body.table_captions).toContain("Table 1A: Permitted uses by zone — Residential");
  expect(body.table_captions).toContain("Table 1B: Permitted uses by zone — Mixed Use");
});


test("retrieval returns zone-specific cell for restaurant use", async ({ request }) => {
  const response = await request.post(`${E2E_API_URL}/v1/_test/search-tables`, {
    headers: { "Content-Type": "application/json" },
    data: {
      bylaw_name: "Regional Centre Land Use By-law",
      use_name: "Restaurant use",
      zone: "DH",
    },
  });
  expect(response.status()).toBe(200);

  const body = await response.json();
  expect(body.candidate_count).toBeGreaterThan(0);

  const texts = body.candidates.map((c: { text: string }) => c.text);
  const hasRestaurant = texts.some((t: string) => /restaurant use/i.test(t));
  expect(hasRestaurant).toBe(true);
});


test("retrieval returns candidates for office use in CEN-2", async ({ request }) => {
  const response = await request.post(`${E2E_API_URL}/v1/_test/search-tables`, {
    headers: { "Content-Type": "application/json" },
    data: {
      bylaw_name: "Regional Centre Land Use By-law",
      use_name: "Office use",
      zone: "CEN-2",
    },
  });
  expect(response.status()).toBe(200);

  const body = await response.json();
  expect(body.candidate_count).toBeGreaterThan(0);

  const texts = body.candidates.map((c: { text: string }) => c.text);
  const hasOffice = texts.some((t: string) => /office use/i.test(t));
  expect(hasOffice).toBe(true);
});
