// Functional regression: ABS-469 — an address that does not exist is refused
// and corrected, and a zone next to a zone line says so.
//
// Scope
// -----
// ABS-466 made a weak resolution visible; the answer hedges. It did not make
// resolution correct. A geocoder answers "567 Windsor Street" by estimating a
// position from the surrounding civic numbering — the civic number was never
// found, because Windsor Street's published address ranges start at 2000 —
// and the point it invents lands on somebody else's parcel. Hedging on that
// is still a wrong answer with a disclaimer attached.
//
// So `get_address_profile` now asks the municipality's own street data
// whether the civic number exists BEFORE the address is geocoded, and reports
// two things a perfect geocode cannot make safe on its own: how close the
// resolved point is to a different zone, and whether the parcel is split
// between zones.
//
// Approach
// --------
// Seed via scripts/seed_e2e_rclub_unified.py, which now also ingests a
// `role: road_centerlines` layer carrying HRM's real published ranges for
// Windsor and Oxford Streets, a second zoning polygon (CEN-1) sharing the
// HR-2 box's eastern edge, and two lots against that edge. Then POST to
// /v1/_test/address-profile and assert on the real DTO over the real
// FastAPI + Postgres/PostGIS path — the PostGIS distance and intersection
// queries behind these fields have no other coverage, since the unit suite
// runs the shapely fallback.

import { execSync } from "node:child_process";
import * as path from "node:path";

import { E2E_API_URL, expect, test } from "../fixtures/test-env";
import { resolveDatabaseUrl } from "../helpers/database-url";

type AddressProfile = {
  address: string;
  civic_number: string | null;
  street: string | null;
  zone: string | null;
  overlays: unknown[];
  citations: unknown[];
  unresolvable: boolean;
  outside_mapped_area: boolean;
  caveats: string[];
  civic_address_status: "confirmed" | "not_found" | "unverifiable" | null;
  civic_address_evidence: string | null;
  valid_civic_number_ranges: string[];
  suggested_civic_numbers: string[];
  zone_boundary_distance_m: number | null;
  nearest_other_zone: string | null;
  parcel_zones: string[];
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

test("a civic number no street segment carries is refused, not answered", async ({
  request,
}) => {
  const profile = await postProfile(request, "567 Windsor Street");

  // The seeded geocode row parks this address INSIDE the HR-2 polygon, so a
  // profile that trusted the point would report a zone with a straight face.
  expect(profile.civic_address_status).toBe("not_found");
  expect(profile.zone).toBeNull();
  expect(profile.overlays).toEqual([]);
  expect(profile.citations).toEqual([]);

  // ...and the refusal names its evidence rather than asserting bare.
  expect(profile.civic_address_evidence).toContain("street_centerline_ranges");
  expect(profile.caveats.join(" ").toLowerCase()).toContain("does not exist");
});

test("the refusal carries the civic numbers that do exist on that street", async ({
  request,
}) => {
  const profile = await postProfile(request, "567 Windsor Street");

  // Windsor Street's segments publish 2000-2089. A user who mistyped needs
  // the correction, not just the rejection.
  expect(profile.valid_civic_number_ranges).toEqual(["2001-2089"]);
  expect(profile.suggested_civic_numbers).toEqual(["2001"]);
});

test("a non-existent address is a different state from an out-of-coverage one", async ({
  request,
}) => {
  const nonexistent = await postProfile(request, "567 Windsor Street");
  const outsideCoverage = await postProfile(request, "500 Nowhere Road");

  // Both have no zone, and they mean opposite things: one address is not
  // real, the other is real but the corpus does not map where it sits. An
  // answer that collapses them sends the user to the wrong place.
  expect(nonexistent.zone).toBeNull();
  expect(outsideCoverage.zone).toBeNull();

  expect(nonexistent.civic_address_status).toBe("not_found");
  expect(nonexistent.outside_mapped_area).toBe(false);
  expect(nonexistent.unresolvable).toBe(false);

  expect(outsideCoverage.outside_mapped_area).toBe(true);
  expect(outsideCoverage.civic_address_status).not.toBe("not_found");
  expect(outsideCoverage.caveats.join(" ").toLowerCase()).toContain(
    "outside every mapped",
  );
});

test("a civic number the street data covers is confirmed and still answered", async ({
  request,
}) => {
  const profile = await postProfile(request, "1234 Oxford Street");

  expect(profile.civic_address_status).toBe("confirmed");
  expect(profile.zone).toBe("HR-2");
  expect(profile.valid_civic_number_ranges).toEqual([]);
});

test("a street the data has never heard of is never called non-existent", async ({
  request,
}) => {
  // Robie Street is deliberately absent from the seeded centerlines. HRM's
  // own ranges have gaps — Nora Bernard Street's 5440-5549 stretch is real
  // and unpublished — so silence about a street can never be evidence
  // against an address on it.
  const profile = await postProfile(request, "100 Robie Street");

  expect(profile.civic_address_status).toBe("unverifiable");
  expect(profile.zone).toBe("HR-2");
});

test("a rooftop match beside a zone line reports how far the line is", async ({
  request,
}) => {
  const profile = await postProfile(request, "7 Boundary Street");

  // Mirrors 6321 Quinpool Road in the live corpus: a ROOFTOP match, squarely
  // inside its zone, 7.6 m from the next one. The geocode is perfect and the
  // answer still is not safe.
  expect(profile.zone).toBe("HR-2");
  expect(profile.nearest_other_zone).toBe("CEN-1");
  expect(profile.zone_boundary_distance_m).not.toBeNull();
  expect(profile.zone_boundary_distance_m!).toBeGreaterThan(5);
  expect(profile.zone_boundary_distance_m!).toBeLessThan(15);
  expect(profile.caveats.join(" ").toLowerCase()).toContain("boundary");
});

test("a parcel split across two zones is reported as split", async ({ request }) => {
  const profile = await postProfile(request, "7 Boundary Street");

  // The lot straddles the HR-2 / CEN-1 line, so which zone governs depends on
  // where on the lot the work sits — there is no single right answer to give.
  expect(profile.parcel_zones.sort()).toEqual(["CEN-1", "HR-2"]);
  expect(profile.caveats.join(" ").toLowerCase()).toContain("split");
});

test("a parcel that merely touches the zone line is not reported as split", async ({
  request,
}) => {
  const profile = await postProfile(request, "9 Boundary Street");

  // Zone polygons share their edges, so almost every lot picks up a sliver of
  // the neighbouring zone from coordinate precision alone. Reporting those
  // would fire on most of the fabric and train the reader to ignore the field.
  expect(profile.zone).toBe("HR-2");
  expect(profile.parcel_zones).toEqual([]);
});
