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
  abuts_pedestrian_street: boolean | null;
  overlays: Overlay[];
  citations: Citation[];
  unresolvable: boolean;
};


function runSeed(): void {
  const repoRoot = path.resolve(__dirname, "..", "..", "..");
  const seed = path.join(repoRoot, "scripts", "seed_e2e_pocs.py");
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
  runSeed();
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
