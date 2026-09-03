// Functional regression: ABS-473 — a height precinct is cited to the by-law
// that governs it, on a parcel whose zone is perfectly well held.
//
// Scope
// -----
// Split out of ABS-472, which found `halifax_zoning_boundaries` linked
// wholesale to the Regional Centre LUB despite being HRM-wide. Every sibling
// geo layer carries the same dataset-level `links_to.document_match`, so each
// needed the same check. Five of six are clean. `halifax_height_precincts` is
// not: it carries its own BYLAW_AREA attribute and 48 of its 1,822 precincts
// say 24 — the Suburban Housing Accelerator LUB, a by-law this corpus does
// not hold. All 1,822 were served as Schedule 15 of the Regional Centre LUB.
//
// Why this needs its own spec rather than a case in abs472's
// ---------------------------------------------------------
// ABS-472's fixture refuses the ZONE, and everything downstream of a refused
// zone is already loud. Here the zone is Regional Centre, held, and correctly
// cited — `governing_bylaw_status` reads "held", the geocode is rooftop, the
// address exists, the lot is not split. Every signal ABS-466/469/472 added
// reads this profile as clean, and a max-height answer still comes out of a
// by-law that does not govern the ground. A zone-level assertion cannot see
// it, which is exactly how it survived ABS-472.
//
// Approach
// --------
// scripts/seed_e2e_rclub_unified.py gives the height-precinct layer the same
// per-feature attribution the real one now resolves from BYLAW_AREA, across
// two precincts: one Regional Centre over the main test point, and one
// Suburban Housing Accelerator over a fourth box east — under an HR-1 zone
// that IS held. Assertions run against the real DTO over the real FastAPI +
// Postgres/PostGIS path, where the per-feature document resolution runs on
// real rows in the real retrieval scope.

import { execSync } from "node:child_process";
import * as path from "node:path";

import { E2E_API_URL, expect, test } from "../fixtures/test-env";
import { resolveDatabaseUrl } from "../helpers/database-url";

type Citation = {
  backs: string[];
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
  governing_bylaw: string | null;
  governing_bylaw_held: boolean | null;
};

type AddressProfile = {
  address: string;
  zone: string | null;
  height_precinct: string | null;
  overlays: Overlay[];
  citations: Citation[];
  caveats: string[];
  resolution_quality: string | null;
  outside_mapped_area: boolean;
  unresolvable: boolean;
  civic_address_status: string | null;
  governing_bylaw: string | null;
  governing_bylaw_status: "held" | "not_held" | "unknown" | null;
};

const HELD_BYLAW = "Regional Centre Land Use By-law";
const UNHELD_BYLAW = "Suburban Housing Accelerator Land Use By-law";

// Inside the ABS-473 box: HR-1 zoning (held) under an SHA height precinct.
const MIXED_ADDRESS = "15 Accelerator Way";
// The main fixture point: Regional Centre on both layers.
const CLEAN_ADDRESS = "100 Robie Street";

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

function heightOverlay(profile: AddressProfile): Overlay {
  const overlay = profile.overlays.find((o) => o.kind === "height_precinct");
  expect(overlay, "expected a height_precinct overlay").toBeTruthy();
  return overlay!;
}

function heightCitations(profile: AddressProfile): Citation[] {
  return profile.citations.filter((c) => c.backs.includes("height_precinct"));
}

test.beforeAll(() => {
  runSeed();
});

test("a height precinct from an unheld by-law is not cited to Schedule 15", async ({
  request,
}) => {
  const profile = await postProfile(request, MIXED_ADDRESS);

  // The precinct itself stays. HRM published it and the mapped height is
  // theirs; telling the user it exists is correct.
  expect(profile.height_precinct).not.toBeNull();

  const height = heightOverlay(profile);
  expect(height.governing_bylaw).toBe(UNHELD_BYLAW);
  expect(height.governing_bylaw_held).toBe(false);
  // Before ABS-473 this carried "Schedule 15" — the RC-LUB's schedule, for a
  // precinct the RC-LUB does not govern.
  expect(height.citation).toBeNull();
  expect(heightCitations(profile)).toEqual([]);
  for (const citation of profile.citations) {
    expect(citation.citation_label).not.toBe("Schedule 15");
  }
});

test("the zone stays held and cited while its height precinct does not", async ({
  request,
}) => {
  const profile = await postProfile(request, MIXED_ADDRESS);

  // This is the whole reason ABS-473 is a separate issue. ABS-472's state is
  // clean here: nothing is wrong with the zone.
  expect(profile.zone).toBe("HR-1");
  expect(profile.governing_bylaw).toBe(HELD_BYLAW);
  expect(profile.governing_bylaw_status).toBe("held");

  const zoneCited = profile.citations.filter((c) => c.backs.includes("zone"));
  expect(zoneCited.length).toBeGreaterThan(0);

  // ...and the profile is still not answerable for height.
  expect(profile.caveats.length).toBeGreaterThan(0);
});

test("a perfect geocode does not suppress the overlay caveat", async ({
  request,
}) => {
  const profile = await postProfile(request, MIXED_ADDRESS);

  expect(profile.resolution_quality).toBe("rooftop");
  expect(profile.unresolvable).toBe(false);
  expect(profile.outside_mapped_area).toBe(false);
  expect(profile.civic_address_status).not.toBe("not_found");

  const caveats = profile.caveats.join(" ");
  expect(caveats).toContain(UNHELD_BYLAW);
  expect(caveats).toContain("height precinct");
  // It must refuse the held schedule by name. "Schedule 15 says 26 m" is the
  // wrong answer available closest to hand, and the one the layer's own link
  // points at.
  expect(caveats).toContain("Schedule 15");
  expect(caveats.toLowerCase()).toContain("not in this corpus");
});

test("a precinct under the by-law we do hold keeps its Schedule 15 citation", async ({
  request,
}) => {
  const profile = await postProfile(request, CLEAN_ADDRESS);

  // The other 1,774 precincts must be untouched: the fix is per-feature
  // attribution, not a blanket retreat from citing Schedule 15.
  const height = heightOverlay(profile);
  expect(height.governing_bylaw).toBe(HELD_BYLAW);
  expect(height.governing_bylaw_held).toBe(true);
  expect(height.citation).toBe("Schedule 15");

  const cited = heightCitations(profile);
  expect(cited.length).toBeGreaterThan(0);
  const rc = cited.find((c) => (c.bylaw_name ?? "").includes("Regional Centre"));
  expect(rc, "expected a Regional Centre height citation").toBeTruthy();
  expect(rc!.citation_label).toBe("Schedule 15");
  expect(rc!.document_id).not.toBeNull();
});

test("the two precincts are distinguishable from the profile alone", async ({
  request,
}) => {
  // A caller has to tell "this precinct is backed by a by-law we hold" from
  // "this precinct is real and its by-law is elsewhere" without reading
  // prose — and crucially, without the zone-level status helping, because it
  // says "held" in both cases.
  const clean = await postProfile(request, CLEAN_ADDRESS);
  const mixed = await postProfile(request, MIXED_ADDRESS);

  expect(clean.height_precinct).not.toBeNull();
  expect(mixed.height_precinct).not.toBeNull();
  expect(clean.governing_bylaw_status).toBe("held");
  expect(mixed.governing_bylaw_status).toBe("held");

  expect(heightOverlay(clean).governing_bylaw_held).toBe(true);
  expect(heightOverlay(mixed).governing_bylaw_held).toBe(false);
  expect(heightCitations(clean).length).toBeGreaterThan(0);
  expect(heightCitations(mixed).length).toBe(0);

  // Case-folded: the caveat shouts "NOT in this corpus" at the model on
  // purpose, so a case-sensitive match would fail the positive assertion and
  // pass the negative one for the wrong reason.
  expect(clean.caveats.join(" ").toLowerCase()).not.toContain(
    "not in this corpus",
  );
  expect(mixed.caveats.join(" ").toLowerCase()).toContain("not in this corpus");
});

test("the coverage audit answers for the height layer, not just zoning", async ({
  request,
}) => {
  // The point of the ABS-473 audit: this report has to cover every layer that
  // carries per-feature attribution, or the next layer added reintroduces the
  // defect and the ops surface still reads clean. Read through the uncached
  // test endpoint rather than /v1/monitoring/corpus-coherence, which caches
  // for 30s and could answer from a body assembled before this seed ran.
  const response = await request.post(
    `${E2E_API_URL}/v1/_test/governing-bylaw-coverage`,
  );
  expect(
    response.status(),
    `coverage endpoint failed: ${response.status()} ${await response.text()}`,
  ).toBe(200);

  const coverage = (await response.json()) as {
    complete: boolean;
    datasets_checked: number;
    unheld: {
      dataset_name: string;
      governing_bylaw: string;
      feature_count: number;
    }[];
  };

  // Both attributed layers are now checked, where ABS-472 saw only zoning.
  expect(coverage.datasets_checked).toBeGreaterThanOrEqual(2);
  expect(coverage.complete).toBe(false);

  const gap = coverage.unheld.find(
    (row) => row.governing_bylaw === UNHELD_BYLAW,
  );
  expect(gap, `expected a ${UNHELD_BYLAW} gap`).toBeTruthy();
  expect(gap!.dataset_name).toContain("height_precincts");
  expect(gap!.feature_count).toBeGreaterThan(0);
});
