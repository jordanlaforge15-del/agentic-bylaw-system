// Functional: ABS-409 — permission-matrix tables must be citation-addressable
// and zone profiles must enumerate a zone's use column.
//
// Root cause under test (found via a prod miss at 6521 Bayer's Road): the
// Regional Centre LUB's Tables 1A-1D were ingested as ORPHANS — caption text
// in an unaddressed PROSE fragment (citation_path NULL), tables with NULL
// caption/parent_fragment_id, continuation pages misprofiled — so
// lookup_citation("Table 1A") could never resolve and get_zone_profile
// returned empty use lists for symbol-dot zones.
//
// Seeds the orphan state (scripts/seed_e2e_table_citations.py), heals it via
// POST /v1/_test/link-table-captions (the backfill/ingest code path), then
// asserts through the real service endpoints:
//   AC1 — linking claims both matrix slices + the parking table; nothing
//         ambiguous in the seeded layout.
//   AC2 — lookup_citation("Part I > [Table 1A]") resolves with table cells
//         attached; the human-style miss ("Table 1A") suggests the canonical
//         path instead of junk.
//   AC3 — get_zone_profile(DH) enumerates permitted + conditional uses from
//         the matrix union (the continuation-slice row "Military use" must be
//         present) with a caption-linked citation.
//   AC4 — the wrongly-profiled parking table is demoted by the caption-aware
//         re-enrichment, so its "Not required" cells never surface as
//         not_permitted verdicts in the zone profile.

import type { APIRequestContext } from "@playwright/test";
import { execSync } from "node:child_process";
import * as path from "node:path";

import { E2E_API_URL, expect, test } from "../fixtures/test-env";
import { resolveDatabaseUrl } from "../helpers/database-url";

const BYLAW_NAME = "Table Citations Test By-law";

function runSeeds(): void {
  const repoRoot = path.resolve(__dirname, "..", "..", "..");
  const venvPython = path.join(repoRoot, ".venv", "bin", "python");
  const databaseUrl = resolveDatabaseUrl();
  const env = {
    ...process.env,
    DATABASE_URL: databaseUrl,
    PYTHONPATH: `${path.join(repoRoot, "src")}:${process.env.PYTHONPATH || ""}`,
  };
  execSync(
    `"${venvPython}" "${path.join(repoRoot, "scripts", "seed_e2e_table_citations.py")}"`,
    { env, stdio: "inherit" },
  );
}

async function linkCaptions(
  request: APIRequestContext,
): Promise<{ documentId: number; body: Record<string, unknown> }> {
  const response = await request.post(
    `${E2E_API_URL}/v1/_test/link-table-captions`,
    {
      headers: { "Content-Type": "application/json" },
      data: { bylaw_name: BYLAW_NAME },
    },
  );
  expect(
    response.status(),
    `link-table-captions failed: ${await response.text()}`,
  ).toBe(200);
  const body = (await response.json()) as Record<string, unknown>;
  return { documentId: body.document_id as number, body };
}

test.describe("ABS-409 table-citation retrieval", () => {
  test("orphan captions become addressable and zone uses enumerate", async ({
    request,
  }) => {
    runSeeds();
    const { documentId, body } = await linkCaptions(request);

    // AC1 — both captions linked, three tables claimed, no ambiguity.
    expect(body.captions_linked).toBe(2);
    expect(body.tables_claimed).toBe(3);
    expect(body.ambiguous_skipped).toBe(0);

    // AC2a — canonical path resolves with the matrix cells attached.
    const hit = await request.post(`${E2E_API_URL}/v1/_test/lookup-citation`, {
      headers: { "Content-Type": "application/json" },
      data: {
        citation_path: "Part I > [Table 1A]",
        document_id: documentId,
        include_tables: true,
      },
    });
    expect(hit.status()).toBe(200);
    const hitBody = await hit.json();
    expect(hitBody.match).not.toBeNull();
    expect(hitBody.match.text).toContain("Permitted uses by zone");
    const relatedTables = hitBody.match.related_tables ?? [];
    expect(relatedTables.length).toBeGreaterThan(0);
    expect(
      relatedTables.some(
        (t: { cells?: unknown[] }) => (t.cells ?? []).length > 0,
      ),
    ).toBe(true);

    // AC2b — the human-style miss suggests the canonical path.
    const miss = await request.post(`${E2E_API_URL}/v1/_test/lookup-citation`, {
      headers: { "Content-Type": "application/json" },
      data: { citation_path: "Table 1A", document_id: documentId },
    });
    expect(miss.status()).toBe(200);
    const missBody = await miss.json();
    expect(missBody.match).toBeNull();
    expect(missBody.suggestions).toContain("Part I > [Table 1A]");

    // AC3 — zone profile enumerates the matrix union for DH.
    const profileResponse = await request.post(
      `${E2E_API_URL}/v1/_test/zone-profile`,
      {
        headers: { "Content-Type": "application/json" },
        data: { zone: "DH", document_id: documentId, include: ["uses"] },
      },
    );
    expect(profileResponse.status()).toBe(200);
    const profileBody = await profileResponse.json();
    expect(profileBody.unknown_zone).toBe(false);
    const uses = profileBody.profile.uses;
    expect(uses.permitted).toContain("Office use");
    expect(uses.permitted).toContain("Multi-unit dwelling use");
    // The continuation slice's row proves the multi-table union.
    expect(uses.permitted).toContain("Military use");
    const conditionalUses = (uses.conditional ?? []).map(
      (item: { use: string }) => item.use,
    );
    expect(conditionalUses).toContain("Restaurant use");
    // AC4 — no parking-table artifact: "Not required" cells must not have
    // been read as permission verdicts (the parking table was demoted).
    expect(uses.not_permitted ?? []).not.toContain("Restaurant use");
    // Citation resolves to the caption fragment's path.
    const citations = profileBody.profile.citations ?? [];
    expect(
      citations.some(
        (c: { citation_path?: string }) =>
          c.citation_path && c.citation_path.endsWith("[Table 1A]"),
      ),
    ).toBe(true);
  });
});
