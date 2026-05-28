// Functional: ABS-178 — Automated verification of bylaw ingest coverage
//
// Seeds a synthetic 5-page bylaw with known fragments and deliberate gaps
// via seed_e2e_verify_coverage.py, then hits the /v1/_test/verify-coverage
// endpoint to confirm the coverage report detects expected gaps and
// produces the correct report structure.

import { execSync } from "node:child_process";
import * as path from "node:path";

import { E2E_API_URL, expect, test } from "../fixtures/test-env";


let seededDocumentId: number;

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
  const output = execSync(
    `"${venvPython}" "${path.join(repoRoot, "scripts", "seed_e2e_verify_coverage.py")}"`,
    { env, encoding: "utf-8" },
  );
  const match = output.match(/document=(\d+)/);
  if (!match) throw new Error(`seed script did not print document id: ${output}`);
  seededDocumentId = parseInt(match[1], 10);
}


test.beforeAll(() => {
  runSeeds();
});


test("verify-coverage returns a structured report", async ({ request }) => {
  const response = await request.post(`${E2E_API_URL}/v1/_test/verify-coverage`, {
    headers: { "Content-Type": "application/json" },
    data: { document_id: seededDocumentId },
  });
  expect(response.status(), `verify-coverage failed: ${await response.text()}`).toBe(200);

  const body = await response.json();
  expect(body.document_id).toBe(seededDocumentId);
  expect(body.municipality).toBe("HRM");
  expect(body.bylaw_name).toBe("Coverage E2E Bylaw");

  // Summary shape
  expect(body.summary).toBeDefined();
  expect(body.summary.page_count).toBe(5);
  expect(body.summary.total_fragments).toBeGreaterThanOrEqual(3);
  expect(typeof body.summary.grade).toBe("string");
  expect(typeof body.summary.mean_page_overlap).toBe("number");
});


test("verify-coverage detects empty page gap", async ({ request }) => {
  const response = await request.post(`${E2E_API_URL}/v1/_test/verify-coverage`, {
    headers: { "Content-Type": "application/json" },
    data: { document_id: seededDocumentId },
  });
  expect(response.status()).toBe(200);

  const body = await response.json();

  // Page 4 has source text but no fragments — should be flagged
  expect(body.summary.pages_without_fragments).toContain(4);

  const emptyPageGaps = body.gaps.filter(
    (g: { gap_type: string }) => g.gap_type === "empty_page",
  );
  expect(emptyPageGaps.length).toBeGreaterThanOrEqual(1);
  expect(emptyPageGaps.some((g: { page: number }) => g.page === 4)).toBe(true);
});


test("verify-coverage detects uncertain fragments", async ({ request }) => {
  const response = await request.post(`${E2E_API_URL}/v1/_test/verify-coverage`, {
    headers: { "Content-Type": "application/json" },
    data: { document_id: seededDocumentId },
  });
  expect(response.status()).toBe(200);

  const body = await response.json();
  expect(body.summary.uncertain_fragments).toBeGreaterThanOrEqual(1);

  const uncertainGaps = body.gaps.filter(
    (g: { gap_type: string }) => g.gap_type === "uncertain_fragments",
  );
  expect(uncertainGaps.length).toBeGreaterThanOrEqual(1);
});


test("verify-coverage detects unresolved cross-references", async ({ request }) => {
  const response = await request.post(`${E2E_API_URL}/v1/_test/verify-coverage`, {
    headers: { "Content-Type": "application/json" },
    data: { document_id: seededDocumentId },
  });
  expect(response.status()).toBe(200);

  const body = await response.json();
  expect(body.summary.unresolved_cross_references).toBeGreaterThanOrEqual(1);

  const xrefGaps = body.gaps.filter(
    (g: { gap_type: string }) => g.gap_type === "unresolved_cross_references",
  );
  expect(xrefGaps.length).toBeGreaterThanOrEqual(1);
});


test("verify-coverage returns per-page details", async ({ request }) => {
  const response = await request.post(`${E2E_API_URL}/v1/_test/verify-coverage`, {
    headers: { "Content-Type": "application/json" },
    data: { document_id: seededDocumentId },
  });
  expect(response.status()).toBe(200);

  const body = await response.json();
  expect(body.page_details).toHaveLength(5);

  const page1 = body.page_details.find(
    (p: { page_number: number }) => p.page_number === 1,
  );
  expect(page1).toBeDefined();
  expect(page1.fragment_count).toBeGreaterThanOrEqual(1);
  expect(page1.overlap_ratio).toBeGreaterThan(0);
});


test("verify-coverage does not flag structural fragments as missing_citation_path", async ({ request }) => {
  // ABS-205: HEADING/PROSE/LIST_ITEM/FOOTNOTE fragments with citation_path=None
  // are structural by design and must NOT produce a missing_citation_path gap.
  const response = await request.post(`${E2E_API_URL}/v1/_test/verify-coverage`, {
    headers: { "Content-Type": "application/json" },
    data: { document_id: seededDocumentId },
  });
  expect(response.status()).toBe(200);

  const body = await response.json();
  const citationGaps = body.gaps.filter(
    (g: { gap_type: string }) => g.gap_type === "missing_citation_path",
  );
  expect(citationGaps).toHaveLength(0);
  expect(body.summary.fragments_without_citation).toBe(0);
});


test("verify-coverage 404s for nonexistent document", async ({ request }) => {
  const response = await request.post(`${E2E_API_URL}/v1/_test/verify-coverage`, {
    headers: { "Content-Type": "application/json" },
    data: { document_id: 999999 },
  });
  expect(response.status()).toBe(404);
});
