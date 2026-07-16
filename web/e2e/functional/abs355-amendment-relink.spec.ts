// Functional regression: ABS-355 — re-link geo datasets when a bylaw document
// is re-ingested (a new version evicts all layers) — end-to-end through the
// real Postgres/PostGIS stack.
//
// Scope
// -----
// Geo layers pin to a specific document version at ingest time
// (ExternalDataset.linked_fragment_id -> SourceFragment -> Document). Retrieval
// scoping (latest_per_bylaw_resolver + _scoped_linked_datasets) only surfaces
// layers whose pinned document is the newest per (municipality, bylaw_name). So
// re-ingesting an amended bylaw under the same name would silently evict every
// existing layer and make get_address_profile return zone=null for every
// address — the production twin of the ABS-349/350 e2e regression.
//
// The fix runs relink_superseded_datasets at the tail of document ingestion
// (layer1.pipeline.ingest.ingest_file) to re-point superseded layers onto the
// new version. Pytest covers the linker unit paths; this spec exercises the
// PostGIS gate: the seed links a zone layer to v1, re-ingests v2, runs the real
// re-link pass, and this test asserts the address still resolves its zone AND
// that the grounding citations now reference the v2 document — not v1.

import { execSync } from "node:child_process";
import * as path from "node:path";

import { E2E_API_URL, expect, test } from "../fixtures/test-env";


type Citation = {
  backs: string[];
  citation_path: string | null;
  citation_label: string | null;
  document_id: number | null;
  municipality: string | null;
  bylaw_name: string | null;
};

type AddressProfile = {
  address: string;
  zone: string | null;
  citations: Citation[];
  unresolvable: boolean;
};

type SeedSummary = {
  v1_document_id: number;
  v2_document_id: number;
  relinked: number;
};


function runSeed(): SeedSummary {
  const repoRoot = path.resolve(__dirname, "..", "..", "..");
  const seed = path.join(repoRoot, "scripts", "seed_e2e_amendment_relink.py");
  const venvPython = path.join(repoRoot, ".venv", "bin", "python");
  // Honor PG_PORT so this seed lands in the right Postgres when a worktree
  // overrides ports for parallel `make e2e`.
  const pgPort = process.env.PG_PORT || "5432";
  const databaseUrl =
    process.env.DATABASE_URL ||
    `postgresql+psycopg://layer1:layer1@localhost:${pgPort}/layer1_test`;

  const stdout = execSync(`"${venvPython}" "${seed}"`, {
    env: {
      ...process.env,
      DATABASE_URL: databaseUrl,
      PYTHONPATH: `${path.join(repoRoot, "src")}:${path.join(repoRoot, "mcp")}:${process.env.PYTHONPATH || ""}`,
    },
    encoding: "utf-8",
  });
  const match = stdout.match(/seed_e2e_amendment_relink summary: (\{.*\})/);
  expect(match, `seed did not emit a summary line:\n${stdout}`).not.toBeNull();
  return JSON.parse(match![1]) as SeedSummary;
}


async function postProfile(
  request: import("@playwright/test").APIRequestContext,
  address: string,
): Promise<AddressProfile> {
  // The *scoped* endpoint applies production latest-per-bylaw scoping, so a
  // layer still pinned to the superseded document version would fall out of
  // scope and resolve to null — the real eviction this fix prevents.
  const response = await request.post(`${E2E_API_URL}/v1/_test/address-profile-scoped`, {
    headers: { "Content-Type": "application/json" },
    data: { address },
  });
  expect(
    response.status(),
    `address-profile endpoint failed: ${response.status()} ${await response.text()}`,
  ).toBe(200);
  return (await response.json()) as AddressProfile;
}


let summary: SeedSummary;

test.beforeAll(() => {
  summary = runSeed();
});


test("re-ingesting an amended bylaw keeps the zone resolvable via the new version", async ({
  request,
}) => {
  // Sanity: the seed re-pointed exactly one layer during the v2 re-ingest.
  expect(summary.relinked).toBe(1);
  expect(summary.v2_document_id).toBeGreaterThan(summary.v1_document_id);

  const profile = await postProfile(request, "355 Amendment Avenue");

  // Before the fix this returned zone=null because the layer was still pinned
  // to v1, which latest_per_bylaw_resolver had evicted from scope.
  expect(profile.unresolvable).toBe(false);
  expect(profile.zone).toBe("AR-9");

  // The grounding citation must now reference the v2 document — proof the
  // layer followed the amendment rather than pointing at the evicted v1.
  const zoneCitations = profile.citations.filter((c) => c.backs.includes("zone"));
  expect(zoneCitations.length).toBeGreaterThan(0);
  for (const citation of zoneCitations) {
    expect(citation.bylaw_name).toBe("ABS-355 Amendment Bylaw");
    expect(citation.document_id).toBe(summary.v2_document_id);
    expect(citation.document_id).not.toBe(summary.v1_document_id);
  }
});
