// Functional regression: ABS-273 — get_address_profile (Phase 3 thick tool)
// end-to-end through the real-stack HTTP path.
//
// Scope
// -----
// The thick get_address_profile tool collapses address -> zone spatial
// resolution plus every linked-overlay lookup (height precinct, FAR
// precinct, heritage) into one call. Pytest covers the service + tool
// dispatch layers; this spec exercises the HTTP path through the running
// FastAPI test server so a missing migration, an unpopulated PostGIS
// geometry column, or a proxy misconfig trips e2e instead of staying
// invisible until production. (AC-3.6)
//
// Approach
// --------
// 1. beforeAll — invoke scripts/seed_e2e_address_profile.py to insert a
//    synthetic Regional Centre doc + four linked overlays + a geocode-cache
//    row into the test Postgres.
// 2. Each test posts to POST /v1/_test/address-profile (a test-only endpoint
//    mounted in advisor.api.e2e_server) and asserts on the AddressProfile.

import { execSync } from "node:child_process";
import * as path from "node:path";

import { E2E_API_URL, expect, test } from "../fixtures/test-env";


type Citation = {
  source: string;
  citation_path: string | null;
  citation_label: string | null;
  document_id: number | null;
  municipality: string | null;
  bylaw_name: string | null;
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
  pid: string | null;
  zone: string | null;
  zone_chapter: string | null;
  height_precinct: string | null;
  far_precinct: string | null;
  heritage: boolean | null;
  bonus_zoning_eligible: boolean | null;
  overlays: Overlay[];
  citations: Citation[];
  unresolvable: boolean;
};


function runSeed(): void {
  const repoRoot = path.resolve(__dirname, "..", "..", "..");
  const seed = path.join(repoRoot, "scripts", "seed_e2e_address_profile.py");
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


test("resolves a known address into zone + overlay precincts with citations", async ({
  request,
}) => {
  const profile = await postProfile(request, "100 Robie Street");

  expect(profile.unresolvable).toBe(false);
  expect(profile.zone).toBe("HR-2");
  expect(profile.civic_number).toBe("100");
  expect(profile.street).toBe("Robie Street");

  // Overlay precincts the seed places over the test point.
  expect(profile.height_precinct).toBe("HP-25");
  expect(profile.far_precinct).toBe("FA-3.5");
  expect(profile.heritage).toBe(true);

  // Every contributing overlay must carry a citation — the grounding
  // contract the issue calls out ("returns ... citations in one call").
  expect(profile.citations.length).toBeGreaterThan(0);
  const sources = new Set(profile.citations.map((c) => c.source));
  expect(sources).toEqual(
    new Set(["zone", "height_precinct", "far_precinct", "heritage"]),
  );
  for (const citation of profile.citations) {
    expect(citation.citation_path).toBeTruthy();
    expect(citation.document_id).toBeGreaterThan(0);
    expect(citation.bylaw_name).toBe("Regional Centre Land Use By-Law");
  }

  const overlayKinds = new Set(profile.overlays.map((o) => o.kind));
  expect(overlayKinds).toEqual(
    new Set(["zone", "height_precinct", "far_precinct", "heritage"]),
  );
});


test("unresolvable address returns a graceful null profile, not an error", async ({
  request,
}) => {
  const profile = await postProfile(request, "asdf qwerty");

  // FR-3.4: never an exception. The endpoint returns 200 with the
  // unresolvable DTO so the agent can fall back to the thin tools.
  expect(profile.unresolvable).toBe(true);
  expect(profile.zone).toBeNull();
  expect(profile.citations).toEqual([]);
  expect(profile.overlays).toEqual([]);
});
