// ABS-433: the fragmented RC-LUB e2e fixtures are unified into ONE document.
//
// Prod carries a single comprehensive Regional Centre Land Use By-Law
// document; before ABS-433 the e2e corpus split that content across three
// fixture documents (permission tables / schedule+geo anchors / zone-profile
// text), so anything sensitive to document scoping (the ABS-413 enabled
// flag, sibling detection, relink) could false-pass against a corpus shape
// prod never has. This spec pins the unification's stop signals end-to-end
// against the real FastAPI ↔ Postgres stack:
//
// 1. Exactly ONE document exists for the unified RC-LUB identity, it is
//    retrieval-enabled, and every retired fragmented identity is absent.
// 2. The permitted-use table search resolves Table 1A/1B cells from that
//    single document, and get_address_profile resolves all six overlay
//    roles (zone / height / FAR / heritage / bonus-zoning / Schedule 7
//    POCS) with every citation carrying the SAME document id.
// 3. Disabling that one document (the real publish surface behind the CLI's
//    disable-retrieval) empties BOTH the production-scoped evidence search
//    and the production-scoped address profile — proving no shadow copy of
//    the Regional Centre content remains anywhere in the corpus — and
//    re-enabling restores them.

import { execSync } from "node:child_process";
import * as path from "node:path";

import { E2E_API_URL, expect, test } from "../fixtures/test-env";

const RCLUB_MUNICIPALITY = "HRM";
const RCLUB_UNIFIED_BYLAW_NAME = "Regional Centre Land Use By-Law (Unified RC-LUB E2E)";

// Retired pre-ABS-433 fixture identities — the unified seed purges these and
// nothing may recreate them.
const RETIRED_IDENTITIES: Array<{ municipality: string; bylaw_name: string }> = [
  { municipality: "HRM", bylaw_name: "Regional Centre Land Use By-law (Permission Tables E2E)" },
  { municipality: "HRM", bylaw_name: "Regional Centre Land Use By-Law (Address Profile E2E)" },
  {
    municipality: "Halifax Regional Municipality",
    bylaw_name: "Regional Centre Land Use By-Law (Zone Profile E2E)",
  },
  {
    municipality: "Halifax Regional Municipality",
    bylaw_name: "Regional Centre Land Use By-Law (POCS Schedule 7 E2E)",
  },
];

// A sentinel string unique to the unified document's zone-profile use rows.
// The shadow-copy probe searches the production-enabled scope for it with NO
// document filter: if any other enabled document carried a copy of the
// Regional Centre content, it would still match after the unified document
// is disabled.
const USES_SENTINEL_QUERY =
  "Use Permissions HR-2 single-unit dwelling secondary suite multi-unit dwelling";

let unifiedDocumentId: number | null = null;

test.beforeAll(() => {
  const repoRoot = path.resolve(__dirname, "..", "..", "..");
  const venvPython = path.join(repoRoot, ".venv", "bin", "python");
  const pgPort = process.env.PG_PORT || "5433";
  const databaseUrl =
    process.env.DATABASE_URL ||
    `postgresql+psycopg://layer1:layer1@localhost:${pgPort}/layer1_test`;
  const env = {
    ...process.env,
    DATABASE_URL: databaseUrl,
    PYTHONPATH: `${path.join(repoRoot, "src")}:${path.join(repoRoot, "mcp")}:${process.env.PYTHONPATH || ""}`,
  };
  const output = execSync(
    `"${venvPython}" "${path.join(repoRoot, "scripts", "seed_e2e_rclub_unified.py")}"`,
    { env, encoding: "utf-8" },
  );
  const m = output.match(/"document_id": (\d+)/);
  unifiedDocumentId = m ? parseInt(m[1], 10) : null;
});

type StatusResponse = {
  documents: { id: number; bylaw_name: string; retrieval_enabled: boolean }[];
};

async function flagStatus(
  request: import("@playwright/test").APIRequestContext,
  municipality: string,
  bylawName: string,
): Promise<StatusResponse> {
  const res = await request.post(`${E2E_API_URL}/v1/_test/retrieval-flag`, {
    headers: { "Content-Type": "application/json" },
    data: { action: "status", municipality, bylaw_name: bylawName },
  });
  expect(res.status()).toBe(200);
  return (await res.json()) as StatusResponse;
}

async function searchEnabledScope(
  request: import("@playwright/test").APIRequestContext,
  query: string,
): Promise<{ matches: { fragment_id: number; document_id: number; text: string }[] }> {
  const res = await request.post(`${E2E_API_URL}/v1/_test/search-enabled-scope`, {
    headers: { "Content-Type": "application/json" },
    data: { query, limit: 20 },
  });
  expect(res.status()).toBe(200);
  return await res.json();
}

async function scopedProfile(
  request: import("@playwright/test").APIRequestContext,
  address: string,
): Promise<{ zone: string | null; overlays: unknown[]; citations: unknown[]; unresolvable: boolean }> {
  const res = await request.post(`${E2E_API_URL}/v1/_test/address-profile-scoped`, {
    headers: { "Content-Type": "application/json" },
    data: { address },
  });
  expect(res.status()).toBe(200);
  return await res.json();
}

test("exactly one RC-LUB document exists; the retired fragmented identities are gone", async ({
  request,
}) => {
  expect(unifiedDocumentId, "seed did not report a document id").not.toBeNull();

  // One document under the unified identity, retrieval-enabled.
  const unified = await flagStatus(request, RCLUB_MUNICIPALITY, RCLUB_UNIFIED_BYLAW_NAME);
  expect(unified.documents).toHaveLength(1);
  expect(unified.documents[0].id).toBe(unifiedDocumentId);
  expect(unified.documents[0].retrieval_enabled).toBe(true);

  // No document exists under any retired fragmented identity.
  for (const identity of RETIRED_IDENTITIES) {
    const status = await flagStatus(request, identity.municipality, identity.bylaw_name);
    expect(
      status.documents,
      `retired fixture identity still present: ${identity.bylaw_name}`,
    ).toHaveLength(0);
  }
});

test("table cells and all six overlay roles resolve from the single document", async ({
  request,
}) => {
  // Permission tables 1A + 1B (incl. the ABS-277 PUA-marker cells) live on
  // the unified document.
  const tablesRes = await request.post(`${E2E_API_URL}/v1/_test/search-tables`, {
    headers: { "Content-Type": "application/json" },
    data: { bylaw_name: RCLUB_UNIFIED_BYLAW_NAME, use_name: "Restaurant use" },
  });
  expect(tablesRes.status(), await tablesRes.text()).toBe(200);
  const tables = await tablesRes.json();
  expect(tables.document_id).toBe(unifiedDocumentId);
  expect(tables.table_count).toBe(2);
  expect(tables.candidate_count).toBeGreaterThan(0);

  // All six overlay roles, each citing the SAME document. 100 Robie sits in
  // every polygon overlay; 6184 Quinpool abuts the Schedule 7 line layer.
  type Profile = {
    citations: { document_id: number | null; backs: string[] }[];
    overlays: { kind: string }[];
  };
  const robieRes = await request.post(`${E2E_API_URL}/v1/_test/address-profile`, {
    headers: { "Content-Type": "application/json" },
    data: { address: "100 Robie Street" },
  });
  expect(robieRes.status()).toBe(200);
  const robie = (await robieRes.json()) as Profile;
  const quinpoolRes = await request.post(`${E2E_API_URL}/v1/_test/address-profile`, {
    headers: { "Content-Type": "application/json" },
    data: { address: "6184 Quinpool Road" },
  });
  expect(quinpoolRes.status()).toBe(200);
  const quinpool = (await quinpoolRes.json()) as Profile;

  const roles = new Set([
    ...robie.overlays.map((o) => o.kind),
    ...quinpool.overlays.map((o) => o.kind),
  ]);
  expect(roles).toEqual(
    new Set([
      "zone",
      "height_precinct",
      "far_precinct",
      "heritage",
      "bonus_zoning",
      "pedestrian_street",
    ]),
  );
  const citedDocIds = new Set(
    [...robie.citations, ...quinpool.citations].map((c) => c.document_id),
  );
  expect(citedDocIds).toEqual(new Set([unifiedDocumentId]));
});

test("disable-retrieval on the one document empties the production scope — no shadow copy", async ({
  request,
}) => {
  expect(unifiedDocumentId).not.toBeNull();
  const docId = unifiedDocumentId as number;

  // Enabled baseline over real HTTP: the sentinel is findable under the
  // production-enabled scope, and the scoped address profile resolves the
  // zone.
  const enabledSearch = await searchEnabledScope(request, USES_SENTINEL_QUERY);
  expect(
    enabledSearch.matches.some((m) => m.document_id === docId),
    "expected the unified document's use-permissions row in the enabled scope",
  ).toBe(true);
  const enabledProfile = await scopedProfile(request, "100 Robie Street");
  expect(enabledProfile.zone).toBe("HR-2");

  // Atomic disable → probe → restore through the real publish surface
  // (set_retrieval_enabled, the function behind the CLI's
  // disable-retrieval). The endpoint holds the corpus advisory lock for the
  // whole transaction, so this can't race a concurrent seed run and never
  // leaves the shared corpus dark for parallel workers.
  const probeRes = await request.post(
    `${E2E_API_URL}/v1/_test/disable-retrieval-probe`,
    {
      headers: { "Content-Type": "application/json" },
      data: {
        document_ids: [docId],
        query: USES_SENTINEL_QUERY,
        address: "100 Robie Street",
      },
    },
  );
  expect(probeRes.status(), await probeRes.text()).toBe(200);
  const probe = (await probeRes.json()) as {
    disabled: {
      matches: { document_id: number; text: string }[];
      matrix_document_ids: number[];
      zone: string | null;
      overlay_count: number;
      citation_count: number;
    };
    restored: {
      matches: { document_id: number; text: string }[];
      matrix_document_ids: number[];
      zone: string | null;
    };
  };

  // Shadow-copy probes, deliberately with NO document filter: any enabled
  // duplicate of the Regional Centre content anywhere in the corpus would
  // still match after THE document is disabled.
  expect(
    probe.disabled.matches.filter((m) => /Use Permissions HR-2/.test(m.text)),
  ).toHaveLength(0);
  expect(probe.disabled.matches.some((m) => m.document_id === docId)).toBe(false);
  // Table scope: the unified document's Table 1A/1B drop out of the
  // production permission-matrix scope. (Other fixture bylaws legitimately
  // stage their own matrices in the shared corpus, so the assertion is on
  // THIS document's membership, not global emptiness.)
  expect(probe.disabled.matrix_document_ids).not.toContain(docId);
  // Geo scope: zone, overlays, and citations all vanish — the six layers
  // hang off the disabled document and nothing else serves them.
  expect(probe.disabled.zone).toBeNull();
  expect(probe.disabled.overlay_count).toBe(0);
  expect(probe.disabled.citation_count).toBe(0);

  // Re-enabling restores every scope.
  expect(probe.restored.zone).toBe("HR-2");
  expect(probe.restored.matrix_document_ids).toContain(docId);
  expect(probe.restored.matches.some((m) => m.document_id === docId)).toBe(true);

  // And the steady state observed over ordinary HTTP is enabled again.
  const restoredProfile = await scopedProfile(request, "100 Robie Street");
  expect(restoredProfile.zone).toBe("HR-2");
});
