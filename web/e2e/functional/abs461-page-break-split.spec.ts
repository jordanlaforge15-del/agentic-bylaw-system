// ABS-461: a PDF page break inside a hyphenated zone code split clause
// 198(1)(a) of the Regional Centre LUB in two. The tail ("2, ER-1, CH-2, ...")
// started with a bare number, so the parser read it as a new section: seven
// clauses -- 198(1)(b) through (f) plus (b)'s sub-clauses -- reparented under
// a phantom "Part V > 2", and 198(1)(a) was stored truncated mid-word.
//
// The user-visible cost, from eval case TC-001: the advisor told a homeowner
// their minimum side setback was 0.0 m, citing the conditional clause (d),
// when the neighbours' zoning meant the catch-all "(f) 2.5 metres elsewhere"
// governed. Clause (f) was sitting under the phantom.
//
// This spec drives /v1/citation against a seeded document whose fragments are
// produced by the real parser (scripts/seed_e2e_page_break_split.py runs
// reconstruct_hierarchy over the verbatim page-171/172 blocks), so a
// regression in the parser guard reproduces the phantom here and fails the
// spec rather than passing on hand-written fixture rows.
//
// Three guarantees, in the order a user hits them:
//
//   1. the phantom is gone -- nothing resolves or is suggested under "Part V > 2"
//   2. clause 198(1)(a) carries the complete zone list, not a text ending "ER-"
//   3. a compact citation ("198(1)(f)") reaches the catch-all clause via the
//      ABS-261 suggestion round trip, which is DoD 4

import { execSync } from "node:child_process";
import * as path from "node:path";

import { test, expect, E2E_API_URL, DEMO_USER_ID } from "../fixtures/test-env";

let documentId: number;

test.beforeAll(() => {
  const repoRoot = path.resolve(__dirname, "..", "..", "..");
  const venvPython = path.join(repoRoot, ".venv", "bin", "python");
  // ABS-207: honor PG_PORT so the seed lands in the right Postgres when a
  // parallel worktree runs on a non-default port triplet.
  const pgPort = process.env.PG_PORT || "5433";
  const databaseUrl =
    process.env.DATABASE_URL ||
    `postgresql+psycopg://layer1:layer1@localhost:${pgPort}/layer1_test`;
  const env = {
    ...process.env,
    DATABASE_URL: databaseUrl,
    PYTHONPATH: `${path.join(repoRoot, "src")}:${process.env.PYTHONPATH || ""}`,
  };
  const stdout = execSync(
    `"${venvPython}" "${path.join(repoRoot, "scripts", "seed_e2e_page_break_split.py")}"`,
    { env, encoding: "utf-8" },
  );
  const match = /document_id=(\d+)/.exec(stdout);
  expect(match, `seed did not report a document_id; stdout: ${stdout}`).not.toBeNull();
  documentId = Number(match![1]);
});

type CitationHit = { citation_path?: string; text?: string };
type CitationMiss = { detail?: { message?: string; suggestions?: string[] } };

async function fetchCitation(
  request: import("@playwright/test").APIRequestContext,
  citationPath: string,
) {
  return await request.get(
    `${E2E_API_URL}/v1/citation?citation_path=${encodeURIComponent(citationPath)}` +
      `&document_id=${documentId}`,
    { headers: { "X-Test-User-Id": DEMO_USER_ID } },
  );
}

/** Resolve a citation the way the tool description tells the agent to: try the
 *  path, and on a miss re-issue with the top suggestion verbatim. */
async function resolveViaSuggestion(
  request: import("@playwright/test").APIRequestContext,
  citationPath: string,
): Promise<CitationHit> {
  const first = await fetchCitation(request, citationPath);
  if (first.status() === 200) {
    return (await first.json()) as CitationHit;
  }
  expect(first.status(), `unexpected status; body: ${await first.text()}`).toBe(404);
  const miss = (await first.json()) as CitationMiss;
  const suggestions = miss.detail?.suggestions ?? [];
  expect(
    suggestions.length,
    `no suggestions offered for ${citationPath}; body: ${JSON.stringify(miss)}`,
  ).toBeGreaterThan(0);
  const second = await fetchCitation(request, suggestions[0]);
  expect(
    second.status(),
    `top suggestion ${suggestions[0]} did not resolve; body: ${await second.text()}`,
  ).toBe(200);
  return (await second.json()) as CitationHit;
}

test.describe("ABS-461: a page break must not forge a section number", () => {
  test("nothing resolves or is suggested under the phantom section", async ({
    request,
  }) => {
    const res = await fetchCitation(request, "Part V > 2");
    expect(res.status()).toBe(404);
    const body = (await res.json()) as CitationMiss;
    const suggestions = body.detail?.suggestions ?? [];
    // The phantom itself is gone, and so is everything that hung off it.
    expect(
      suggestions.filter((s) => s === "Part V > 2" || s.startsWith("Part V > 2 >")),
      `phantom paths survived: ${JSON.stringify(suggestions)}`,
    ).toEqual([]);
  });

  test("clause 198(1)(a) carries the complete zone list across the page break", async ({
    request,
  }) => {
    const hit = await resolveViaSuggestion(request, "198(1)(a)");
    // The break fell inside "ER-2". Pre-fix the stored text stopped at "ER-".
    expect(hit.text).toContain("ER-3, ER-2, ER-1, CH-2, CH-1, PCF, or RPK zone:");
    expect(hit.text?.trimEnd().endsWith("ER-")).toBe(false);
    expect(hit.citation_path).toContain("198");
  });

  test("the catch-all side setback resolves from a compact citation", async ({
    request,
  }) => {
    // DoD 4. This is the clause TC-001 needed and could not reach: with the
    // conditional clauses inapplicable, "(f) 2.5 metres elsewhere" governs.
    const hit = await resolveViaSuggestion(request, "198(1)(f)");
    expect(hit.text).toContain("2.5 metres elsewhere");
    expect(hit.citation_path).toContain("198");
    expect(hit.citation_path?.startsWith("Part V > 2 >")).toBe(false);
  });

  test("the conditional clause (d) is a sibling of (f), not a phantom's child", async ({
    request,
  }) => {
    // (d) is the 0.0 m clause the advisor wrongly applied. It has to sit under
    // the same subsection as (f) so a reader can see (f) is its alternative.
    const conditional = await resolveViaSuggestion(request, "198(1)(d)");
    const catchAll = await resolveViaSuggestion(request, "198(1)(f)");
    expect(conditional.text).toContain("0.0 metre");

    const parentOf = (citationPath?: string) =>
      (citationPath ?? "").split(" > ").slice(0, -1).join(" > ");
    expect(parentOf(conditional.citation_path)).toBe(parentOf(catchAll.citation_path));
    expect(parentOf(catchAll.citation_path)).not.toBe("");
  });
});
