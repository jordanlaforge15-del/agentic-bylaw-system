// Functional: ABS-163 — Permission tables are found by the retrieval layer
// when they carry proper captions (e.g. "Table 1A: Permitted uses by zone").
//
// Seeds the unified RC-LUB e2e document (ABS-433) whose Table 1A / 1B
// SourceTable rows carry proper captions,
// then hits the /v1/_test/search-tables endpoint to confirm the retrieval
// layer's _structured_permission_table_candidates() finds them by caption
// pattern and returns the correct cells for a given use + zone query.

import { execSync } from "node:child_process";
import * as path from "node:path";

import { E2E_API_URL, expect, test } from "../fixtures/test-env";
import { resolveDatabaseUrl } from "../helpers/database-url";

// ABS-433: the single unified RC-LUB e2e document (scripts/seed_e2e_rclub_unified.py).
const RCLUB_UNIFIED_BYLAW_NAME = "Regional Centre Land Use By-Law (Unified RC-LUB E2E)";


function runSeeds(): void {
  const repoRoot = path.resolve(__dirname, "..", "..", "..");
  const venvPython = path.join(repoRoot, ".venv", "bin", "python");
  // ABS-207: honor PG_PORT for the parallel-worktree case.
  const databaseUrl = resolveDatabaseUrl();
  const env = {
    ...process.env,
    DATABASE_URL: databaseUrl,
    PYTHONPATH: `${path.join(repoRoot, "src")}:${process.env.PYTHONPATH || ""}`,
  };
  execSync(
    `"${venvPython}" "${path.join(repoRoot, "scripts", "seed_e2e_rclub_unified.py")}"`,
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
      bylaw_name: RCLUB_UNIFIED_BYLAW_NAME,
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
      bylaw_name: RCLUB_UNIFIED_BYLAW_NAME,
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


// ABS-277: the "Home occupation use" row is seeded with symbol-font markers —
// DD is the U+F098 dot (renders blank), DH is a circled-three conditional, ER-3
// is empty. The seed's recovery pass must classify them into
// metadata_json.permission_marker, surfaced here via the search-tables cells[].
test("recovers symbol-font permission markers into permission_marker", async ({
  request,
}) => {
  const response = await request.post(`${E2E_API_URL}/v1/_test/search-tables`, {
    headers: { "Content-Type": "application/json" },
    data: {
      bylaw_name: RCLUB_UNIFIED_BYLAW_NAME,
      use_name: "Home occupation use",
    },
  });
  expect(response.status(), `search-tables failed: ${await response.text()}`).toBe(200);

  const body = await response.json();
  type Cell = {
    row_header_path: string | null;
    col_header_path: string | null;
    permission_marker: string | null;
    footnote: number | null;
  };
  const homeCells: Cell[] = body.cells.filter(
    (c: Cell) => c.row_header_path === "Home occupation use",
  );
  const byZone = (zone: string) =>
    homeCells.find((c) => c.col_header_path === zone);

  // U+F098 dot -> permitted (the core ABS-277 recovery).
  expect(byZone("DD")?.permission_marker).toBe("permitted");
  // Circled-three -> conditional with footnote ordinal 3.
  expect(byZone("DH")?.permission_marker).toBe("conditional");
  expect(byZone("DH")?.footnote).toBe(3);
  // Empty cell -> not_permitted.
  expect(byZone("ER-3")?.permission_marker).toBe("not_permitted");

  // Header / row-label cells carry no marker semantics.
  const headerCell = body.cells.find(
    (c: Cell) => c.row_header_path === null && c.col_header_path === "DD",
  );
  expect(headerCell?.permission_marker ?? null).toBeNull();
});


test("retrieval returns candidates for office use in CEN-2", async ({ request }) => {
  const response = await request.post(`${E2E_API_URL}/v1/_test/search-tables`, {
    headers: { "Content-Type": "application/json" },
    data: {
      bylaw_name: RCLUB_UNIFIED_BYLAW_NAME,
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
