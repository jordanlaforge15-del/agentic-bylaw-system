// Functional regression: ABS-466 — an address's resolution quality reaches
// the caller, and a weak resolution can no longer present as a fact.
//
// Scope
// -----
// A user's address becomes a point, the point selects a zoning polygon, and
// that zone drives every setback, height and FAR answer downstream. Before
// this issue `AddressProfile` carried no confidence, no resolver and no
// location type, so an INTERPOLATED point (the geocoder never found the civic
// number — it estimated a position along the street) came back looking
// exactly like a rooftop match. Near a zone boundary that estimate lands on
// the neighbouring parcel and the advisor states the wrong zone confidently.
//
// The same issue's fourth defect: an address could resolve to a point that
// fell outside every mapped boundary and still return `unresolvable: false`
// with `zone: null` and zero overlays — a state the model could only read as
// "this property has no zone".
//
// Approach
// --------
// Seed via scripts/seed_e2e_rclub_unified.py (which now carries an
// interpolated fixture address alongside the rooftop one), then POST to
// /v1/_test/address-profile and assert on the real DTO over the real
// FastAPI + Postgres/PostGIS path — so a schema field that never got
// serialised, or a service that stopped populating it, trips e2e.

import { execSync } from "node:child_process";
import * as path from "node:path";

import { E2E_API_URL, expect, test } from "../fixtures/test-env";
import { resolveDatabaseUrl } from "../helpers/database-url";

type AddressProfile = {
  address: string;
  zone: string | null;
  overlays: unknown[];
  citations: unknown[];
  unresolvable: boolean;
  resolution_quality:
    | "rooftop"
    | "interpolated"
    | "centroid"
    | "approximate"
    | "unknown"
    | null;
  location_confidence: number | null;
  location_type: string | null;
  location_resolver: string | null;
  outside_mapped_area: boolean;
  caveats: string[];
};

function runSeed(): void {
  const repoRoot = path.resolve(__dirname, "..", "..", "..");
  const seed = path.join(repoRoot, "scripts", "seed_e2e_rclub_unified.py");
  const venvPython = path.join(repoRoot, ".venv", "bin", "python");
  const databaseUrl = resolveDatabaseUrl();

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

test("a precise match reports rooftop quality and asks for no qualification", async ({
  request,
}) => {
  const profile = await postProfile(request, "100 Robie Street");

  expect(profile.unresolvable).toBe(false);
  expect(profile.zone).toBe("HR-2");
  expect(profile.resolution_quality).toBe("rooftop");
  expect(profile.location_confidence).toBeGreaterThanOrEqual(0.95);
  expect(profile.location_resolver).toBeTruthy();
  expect(profile.outside_mapped_area).toBe(false);
  // Nothing was estimated, so there is nothing to hedge.
  expect(profile.caveats).toEqual([]);
});

test("an interpolated match resolves a zone but flags that the point was estimated", async ({
  request,
}) => {
  const profile = await postProfile(request, "1234 Oxford Street");

  // The zone still resolves — this is not a refusal...
  expect(profile.unresolvable).toBe(false);
  expect(profile.zone).toBe("HR-2");
  // ...but the caller is told the point behind it was a guess.
  expect(profile.resolution_quality).toBe("interpolated");
  expect(profile.location_type).toBe("RANGE_INTERPOLATED");
  expect(profile.location_confidence).toBeCloseTo(0.85, 5);
  expect(profile.caveats.length).toBeGreaterThan(0);

  const caveat = profile.caveats.join(" ").toLowerCase();
  expect(caveat).toContain("estimated");
  expect(caveat).toContain("neighbouring parcel");
  expect(caveat).toContain("hrm");
});

test("a point outside every mapped boundary is its own state, not a silent null zone", async ({
  request,
}) => {
  const profile = await postProfile(request, "500 Nowhere Road");

  // It DID resolve to a point — so "unresolvable" would be a lie...
  expect(profile.unresolvable).toBe(false);
  expect(profile.resolution_quality).not.toBeNull();
  // ...but nothing in the corpus covers it, and that is now explicit rather
  // than an unexplained `zone: null` with zero overlays.
  expect(profile.zone).toBeNull();
  expect(profile.overlays).toEqual([]);
  expect(profile.outside_mapped_area).toBe(true);
  expect(profile.caveats.join(" ").toLowerCase()).toContain(
    "outside every mapped",
  );
});

test("an unresolvable address stays distinct from an out-of-coverage one", async ({
  request,
}) => {
  const profile = await postProfile(request, "asdf qwerty");

  expect(profile.unresolvable).toBe(true);
  expect(profile.outside_mapped_area).toBe(false);
  expect(profile.zone).toBeNull();
  expect(profile.resolution_quality).toBeNull();
  expect(profile.caveats.join(" ").toLowerCase()).toContain("do not state a zone");
});
