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
let seededOldDocumentId: number;

function runSeeds(): void {
  const repoRoot = path.resolve(__dirname, "..", "..", "..");
  const venvPython = path.join(repoRoot, ".venv", "bin", "python");
  // ABS-207: honor PG_PORT for the parallel-worktree case.
  const pgPort = process.env.PG_PORT || "5433";
  const databaseUrl =
    process.env.DATABASE_URL ||
    `postgresql+psycopg://layer1:layer1@localhost:${pgPort}/layer1_test`;
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

  const oldMatch = output.match(/old_document=(\d+)/);
  if (!oldMatch) throw new Error(`seed script did not print old_document id: ${output}`);
  seededOldDocumentId = parseInt(oldMatch[1], 10);
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


// ABS-214: --compare-to / compare_to endpoint tests

test("verify-coverage with compare_to returns comparison section", async ({ request }) => {
  // Compare the new document against the old version.
  // Old version was seeded without cross-references, so it has no
  // unresolved_cross_references gap — comparing new vs old shows that
  // gap type as "introduced".
  const response = await request.post(`${E2E_API_URL}/v1/_test/verify-coverage`, {
    headers: { "Content-Type": "application/json" },
    data: { document_id: seededDocumentId, compare_to: seededOldDocumentId },
  });
  expect(response.status(), `compare_to request failed: ${await response.text()}`).toBe(200);

  const body = await response.json();
  expect(body.comparison).toBeDefined();
  expect(body.comparison.old_document_id).toBe(seededOldDocumentId);
  expect(typeof body.comparison.old_grade).toBe("string");
  expect(typeof body.comparison.new_grade).toBe("string");
  expect(body.comparison.summary_deltas).toBeDefined();
  expect(typeof body.comparison.summary_deltas.mean_page_overlap).toBe("string");
  expect(typeof body.comparison.summary_deltas.pages_with_fragments).toBe("string");
});


test("verify-coverage compare_to detects introduced gap types", async ({ request }) => {
  // Old document was seeded without cross-references → no unresolved_cross_references gap.
  // New document has an unresolved cross-reference → that gap type is introduced.
  const response = await request.post(`${E2E_API_URL}/v1/_test/verify-coverage`, {
    headers: { "Content-Type": "application/json" },
    data: { document_id: seededDocumentId, compare_to: seededOldDocumentId },
  });
  expect(response.status()).toBe(200);

  const body = await response.json();
  const introduced: Array<{ gap_type: string; old_count: number; new_count: number }> =
    body.comparison.gaps_introduced;
  const introTypes = introduced.map((g) => g.gap_type);
  expect(introTypes).toContain("unresolved_cross_references");

  const xrefDelta = introduced.find((g) => g.gap_type === "unresolved_cross_references");
  expect(xrefDelta!.old_count).toBe(0);
  expect(xrefDelta!.new_count).toBeGreaterThan(0);
});


test("verify-coverage compare_to self shows all gaps unchanged", async ({ request }) => {
  // When comparing a document to itself, no gaps are introduced or resolved —
  // all gap types appear in both versions and belong in gaps_unchanged.
  const response = await request.post(`${E2E_API_URL}/v1/_test/verify-coverage`, {
    headers: { "Content-Type": "application/json" },
    data: { document_id: seededDocumentId, compare_to: seededDocumentId },
  });
  expect(response.status()).toBe(200);

  const body = await response.json();
  expect(body.comparison.gaps_introduced).toHaveLength(0);
  expect(body.comparison.gaps_resolved).toHaveLength(0);
  expect(body.comparison.gaps_unchanged.length).toBeGreaterThan(0);
});


test("verify-coverage compare_to 404s when old document does not exist", async ({ request }) => {
  const response = await request.post(`${E2E_API_URL}/v1/_test/verify-coverage`, {
    headers: { "Content-Type": "application/json" },
    data: { document_id: seededDocumentId, compare_to: 999998 },
  });
  expect(response.status()).toBe(404);
});
