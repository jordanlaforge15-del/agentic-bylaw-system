// Functional regression: ABS-350 — the pedestrian_street overlay role
// (Schedule 7 Pedestrian-Oriented Commercial Streets) surfaced through
// get_address_profile on the real Postgres/PostGIS stack.
//
// Why this spec exists
// --------------------
// The dev-DB answer for 6184 Quinpool Rd (advisor_question_purchase id 3)
// resolved the zone and confirmed both uses permitted as-of-right, but the
// final determination flips on whether the lot abuts a pedestrian-oriented
// commercial street per Schedule 7: s.38(2) prohibits ground-floor office on a
// POCS street, s.69(d) permits it otherwise. Before this role existed the
// retrieval service structurally could not report the designation, so the
// agent had to deliver a two-scenario conditional answer.
//
// Unlike the precinct overlays (area polygons tested point-in-polygon) the
// POCS layer is LINE geometry, so the service must use an *abuts* predicate
// (ST_DWithin on geography). Pytest covers the sqlite/shapely path; this spec
// proves the PostGIS ST_DWithin path end-to-end — a missing migration, an
// unpopulated geometry column, or an SRID/geography mishap trips e2e instead
// of staying invisible until production.
//
// Approach
// --------
// 1. beforeAll — scripts/seed_e2e_pocs.py ingests the POCS dataset (a Quinpool
//    Road segment + a control Gottingen Street segment) through
//    ingest_geo_dataset (populating the PostGIS geometry column), links it to
//    the Regional Centre LUB "Schedule 7" fragment, and seeds geocode-cache
//    rows for "6184 Quinpool Road" (~10 m off the centreline) and a far-away
//    "500 Nowhere Road" control.
// 2. Each test posts to POST /v1/_test/address-profile and asserts on the
//    AddressProfile's abuts_pedestrian_street branch.
//
// Shared-document regression (ABS-350 seed fix, commits 4f58978 / 24b3803)
// ------------------------------------------------------------------------
// Schedule 7 is part of the Regional Centre Land Use By-Law, the same bylaw the
// address-profile seed uses for its zone/height/FAR/heritage overlays. At the
// time the production advisor scoped retrieval with latest_per_bylaw_resolver —
// only the newest document per (municipality, bylaw_name) stayed in scope
// (since ABS-413 the scope is the operator-enabled document set). An earlier
// version of this seed minted a SECOND, newer "Regional Centre Land Use By-Law"
// document, which evicted the zone-overlay document and regressed 100 Robie
// Street to zone=null (breaking abs274-bylaw-query / address-profile-mcp-tool
// post-merge). The seed now binds to the shared document and purges the stale
// one. This spec runs BOTH seeds — address-profile first, then POCS (the
// eviction ordering) — and the third test asserts the zone and the Schedule 7
// overlay cite the SAME document id, the invariant the fix restores. (The
// /v1/_test endpoint is unscoped, so it observes the shared-document invariant
// rather than the scoped eviction itself; see that test for the full rationale.)

import { execSync } from "node:child_process";
import * as path from "node:path";

import { E2E_API_URL, expect, test } from "../fixtures/test-env";


type Citation = {
  citation_path: string | null;
  citation_label: string | null;
  document_id: number | null;
  municipality: string | null;
  bylaw_name: string | null;
  backs: string[];
};

type Overlay = {
  kind: string;
  dataset_name: string;
  label: string | null;
  citation: string | null;
  attributes: Record<string, unknown>;
};

type AddressProfile = {
  address: string;
  civic_number: string | null;
  street: string | null;
  zone: string | null;
  abuts_pedestrian_street: boolean | null;
  overlays: Overlay[];
  citations: Citation[];
  unresolvable: boolean;
};


function runSeed(scriptName: string): void {
  const repoRoot = path.resolve(__dirname, "..", "..", "..");
  const seed = path.join(repoRoot, "scripts", scriptName);
  const venvPython = path.join(repoRoot, ".venv", "bin", "python");
  // ABS-207: honor PG_PORT so this seed lands in the right Postgres when a
  // worktree overrides ports for parallel `make e2e`.
  const pgPort = process.env.PG_PORT || "5432";
  const databaseUrl =
    process.env.DATABASE_URL ||
    `postgresql+psycopg://layer1:layer1@localhost:${pgPort}/layer1_test`;

  execSync(`"${venvPython}" "${seed}"`, {
    env: {
      ...process.env,
      DATABASE_URL: databaseUrl,
      PYTHONPATH: `${path.join(repoRoot, "src")}:${path.join(repoRoot, "mcp")}:${process.env.PYTHONPATH || ""}`,
    },
    stdio: "inherit",
  });
}


async function postProfile(
  request: import("@playwright/test").APIRequestContext,
  address: string,
): Promise<AddressProfile> {
  const response = await request.post(`${E2E_API_URL}/v1/_test/address-profile`, {
    headers: { "Content-Type": "application/json" },
    data: { address },
  });
  expect(
    response.status(),
    `address-profile endpoint failed: ${response.status()} ${await response.text()}`,
  ).toBe(200);
  return (await response.json()) as AddressProfile;
}


test.beforeAll(() => {
  // Order matters for the non-eviction regression: seed the address-profile
  // document (zone/height/FAR/heritage overlays) FIRST, then the POCS layer.
  // Under the pre-fix seed the POCS document was newer and would evict the
  // zone document from the then-latest-per-bylaw scope; the fix binds both to
  // one shared (retrieval-enabled) document, so this ordering must now leave
  // the zone resolvable.
  runSeed("seed_e2e_address_profile.py");
  runSeed("seed_e2e_pocs.py");
});


test("6184 Quinpool Rd abuts a Schedule 7 pedestrian-oriented commercial street", async ({
  request,
}) => {
  const profile = await postProfile(request, "6184 Quinpool Road");

  expect(profile.unresolvable).toBe(false);
  // The branch-deciding fact: the buffered point abuts the Quinpool corridor,
  // so s.38(2) governs ground-floor use. This is what the dev-DB answer could
  // not establish before the overlay existed.
  expect(profile.abuts_pedestrian_street).toBe(true);

  const pocs = profile.overlays.find((o) => o.kind === "pedestrian_street");
  expect(pocs, "expected a pedestrian_street overlay").toBeTruthy();
  expect(pocs?.label).toBe("Quinpool Road");
  expect(pocs?.citation).toBe("Schedule 7");

  // A citation backs the pedestrian_street facet, tracing to Schedule 7 of the
  // Regional Centre LUB — the grounding the answer must cite.
  const pocsCitation = profile.citations.find((c) => c.backs.includes("pedestrian_street"));
  expect(pocsCitation, "expected a pedestrian_street citation").toBeTruthy();
  expect(pocsCitation?.citation_path).toBe("schedule_7");
  expect(pocsCitation?.bylaw_name).toBe("Regional Centre Land Use By-Law");
});


test("an address off every corridor abuts no Schedule 7 street (definitive false)", async ({
  request,
}) => {
  const profile = await postProfile(request, "500 Nowhere Road");

  expect(profile.unresolvable).toBe(false);
  // Definitive false, NOT null: a Schedule 7 dataset was in scope and no
  // designated segment fell within the abut buffer. This lets the agent apply
  // s.69(d) (ground-floor office permitted) instead of hedging both scenarios.
  expect(profile.abuts_pedestrian_street).toBe(false);
  expect(profile.overlays.some((o) => o.kind === "pedestrian_street")).toBe(false);
});


test("Schedule 7 and the zone overlays share one Regional Centre LUB document", async ({
  request,
}) => {
  // Regression guard for the ABS-350 seed fix (4f58978 / 24b3803).
  //
  // Schedule 7 IS part of the Regional Centre Land Use By-Law that also carries
  // the zoning/height/FAR/heritage overlays. The pre-fix POCS seed minted a
  // SECOND "Regional Centre Land Use By-Law" document (its own file_hash) with a
  // newer ingestion_timestamp. Under the then-production latest_per_bylaw_resolver
  // that newer document evicted the zone document from scope, regressing
  // address_lookup for 100 Robie Street to zone=null on dev post-merge
  // (abs274-bylaw-query / address-profile-mcp-tool). The fix binds the POCS seed
  // to the SHARED document and purges any stale standalone one, so both the zone
  // overlays and Schedule 7 live in a single (municipality, bylaw_name) partition.
  //
  // The /v1/_test endpoint runs an UNSCOPED RetrievalService, so it cannot see
  // the eviction directly (unscoped keeps every document in view). What it CAN
  // observe — and what pre-fix would fail — is the shared-document invariant:
  // the zone facet and the pedestrian_street facet must cite the SAME document
  // id. Two documents for one bylaw (the pre-fix state) would surface two
  // distinct document ids here.
  const robie = await postProfile(request, "100 Robie Street");
  expect(robie.unresolvable).toBe(false);
  expect(robie.zone).toBe("HR-2");

  const zoneCitation = robie.citations.find((c) => c.backs.includes("zone"));
  expect(zoneCitation, "expected a zone citation").toBeTruthy();
  expect(zoneCitation?.bylaw_name).toBe("Regional Centre Land Use By-Law");
  expect(zoneCitation?.document_id).toBeGreaterThan(0);

  const quinpool = await postProfile(request, "6184 Quinpool Road");
  const pocsCitation = quinpool.citations.find((c) =>
    c.backs.includes("pedestrian_street"),
  );
  expect(pocsCitation, "expected a pedestrian_street citation").toBeTruthy();
  expect(pocsCitation?.bylaw_name).toBe("Regional Centre Land Use By-Law");

  // The invariant the seed fix restores: one document holds both overlays.
  expect(pocsCitation?.document_id).toBe(zoneCitation?.document_id);
});
