// Functional regression: ABS-472 — a zone is cited to the by-law that governs
// it, and refused outright when we do not hold that by-law.
//
// Scope
// -----
// `halifax_zoning_boundaries` is HRM-wide: 11,069 features spanning 22 by-law
// areas, all linked wholesale to the Regional Centre LUB. So DH-1 at 1657
// Barrington Street — a Downtown Halifax LUB zone, from a document the corpus
// does not hold at all — came back with a confident zone and a citation
// naming the Regional Centre LUB, which does not govern that parcel. The
// failure was silent: nothing on the profile said the governing by-law was
// missing, so a paid answer reasoned DH-1's setbacks out of the wrong by-law.
//
// This is not a resolution-quality problem. The point is rooftop-perfect and
// the zone code is HRM's own published mapping — both correct. What is
// missing is the document behind the code. Every ABS-466/469 signal reads
// this turn as clean, which is exactly why it needed its own state.
//
// Approach
// --------
// scripts/seed_e2e_rclub_unified.py now gives the zoning layer per-feature
// by-law attribution (`bylaw_area_name` / `bylaw_area_code`, the same
// attributes the real layer resolves from BYLAW_ID) across three polygons:
// HR-2 and CEN-1 under the by-law the unified fixture document IS, and a DH-1
// box under the Downtown Halifax LUB, which has no document. Then POST to
// /v1/_test/address-profile and assert on the real DTO over the real FastAPI
// + Postgres/PostGIS path — the per-feature document resolution runs against
// real rows and the real retrieval scope, which the unit suite's sqlite
// fixtures cannot exercise.

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
  overlays: Overlay[];
  citations: Citation[];
  caveats: string[];
  resolution_quality: string | null;
  outside_mapped_area: boolean;
  unresolvable: boolean;
  civic_address_status: string | null;
  governing_bylaw: string | null;
  governing_bylaw_code: string | null;
  governing_bylaw_status: "held" | "not_held" | "unknown" | null;
};

const HELD_BYLAW = "Regional Centre Land Use By-law";
const UNHELD_BYLAW = "Downtown Halifax Land Use By-law";

function runSeed(): void {
  const repoRoot = path.resolve(__dirname, "..", "..", "..");
  const seed = path.join(repoRoot, "scripts", "seed_e2e_rclub_unified.py");
  const venvPython = path.join(repoRoot, ".venv", "bin", "python");
  const pgPort = process.env.PG_PORT || "5433";
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

function zoneCitations(profile: AddressProfile): Citation[] {
  return profile.citations.filter((c) => c.backs.includes("zone"));
}

test.beforeAll(() => {
  runSeed();
});

test("a zone whose by-law we do not hold is never cited to the layer's document", async ({
  request,
}) => {
  const profile = await postProfile(request, "1657 Barrington Street");

  // The zone itself is real and stays: HRM published it, and telling the user
  // their land is DH-1 is correct.
  expect(profile.zone).toBe("DH-1");
  expect(profile.governing_bylaw).toBe(UNHELD_BYLAW);
  expect(profile.governing_bylaw_code).toBe("hrm:DHFX");
  expect(profile.governing_bylaw_status).toBe("not_held");

  // The citation is what was wrong. No citation at all beats one naming a
  // by-law that does not govern this ground.
  expect(zoneCitations(profile)).toEqual([]);
  for (const citation of profile.citations) {
    expect(citation.bylaw_name).not.toContain("Regional Centre");
  }
});

test("the overlay reports the governing by-law and drops its citation", async ({
  request,
}) => {
  const profile = await postProfile(request, "1657 Barrington Street");

  const zone = profile.overlays.find((o) => o.kind === "zone");
  expect(zone, "expected a zone overlay").toBeTruthy();
  expect(zone!.label).toBe("DH-1");
  expect(zone!.governing_bylaw).toBe(UNHELD_BYLAW);
  expect(zone!.governing_bylaw_held).toBe(false);
  // Before ABS-472 this carried "Zoning Schedule" — the RC-LUB's schedule.
  expect(zone!.citation).toBeNull();
});

test("a perfect geocode does not suppress the missing-by-law caveat", async ({
  request,
}) => {
  const profile = await postProfile(request, "1657 Barrington Street");

  // This is the reason the state exists. The resolution is rooftop, the
  // address is real, the zone is right — every quality signal reads clean,
  // and the answer is still unanswerable.
  expect(profile.resolution_quality).toBe("rooftop");
  expect(profile.unresolvable).toBe(false);
  expect(profile.outside_mapped_area).toBe(false);
  expect(profile.civic_address_status).not.toBe("not_found");

  const caveats = profile.caveats.join(" ");
  expect(caveats).toContain(UNHELD_BYLAW);
  expect(caveats).toContain("DH-1");
  expect(caveats.toLowerCase()).toContain("not in this corpus");
});

test("a zone under the by-law we do hold keeps its citation", async ({
  request,
}) => {
  const profile = await postProfile(request, "100 Robie Street");

  // The 1,910 Regional Centre features must be untouched by this change —
  // the fix is per-feature attribution, not a blanket retreat from citing.
  expect(profile.zone).toBe("HR-2");
  expect(profile.governing_bylaw).toBe(HELD_BYLAW);
  expect(profile.governing_bylaw_status).toBe("held");

  // Not an exact count: the shared e2e database can carry other seeds'
  // zoning layers. What must hold is that the zone IS cited, to the Regional
  // Centre document, under the schedule the layer declares.
  const cited = zoneCitations(profile);
  expect(cited.length).toBeGreaterThan(0);
  const rc = cited.find((c) => (c.bylaw_name ?? "").includes("Regional Centre"));
  expect(rc, "expected a Regional Centre zone citation").toBeTruthy();
  expect(rc!.citation_label).toBe("Zoning Schedule");
  expect(rc!.document_id).not.toBeNull();
});

test("the two outcomes are distinguishable from the profile alone", async ({
  request,
}) => {
  // A caller (the chat agent, a report writer) must be able to tell "this
  // zone is backed by a by-law we hold" from "this zone is real and its
  // by-law is elsewhere" without reading prose.
  const held = await postProfile(request, "100 Robie Street");
  const unheld = await postProfile(request, "1657 Barrington Street");

  expect(held.zone).not.toBeNull();
  expect(unheld.zone).not.toBeNull();

  expect(held.governing_bylaw_status).toBe("held");
  expect(unheld.governing_bylaw_status).toBe("not_held");

  expect(zoneCitations(held).length).toBeGreaterThan(0);
  expect(zoneCitations(unheld).length).toBe(0);

  expect(held.caveats.join(" ")).not.toContain("not in this corpus");
  expect(unheld.caveats.join(" ")).toContain("not in this corpus");
});

test("the coverage audit sizes the unheld ground", async ({ request }) => {
  // Per-address refusal covers the parcel in front of you. The ops surface is
  // what says how much ground is behind it — and which by-law to ingest next.
  // Read through the uncached test endpoint rather than
  // /v1/monitoring/corpus-coherence, which caches for 30s and could answer
  // from a body assembled before this spec's own seed ran.
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
    features_checked: number;
    covered_features: number;
    unheld_features: number;
    unheld: { governing_bylaw: string; feature_count: number }[];
  };

  expect(coverage.datasets_checked).toBeGreaterThan(0);
  expect(coverage.complete).toBe(false);
  // HR-2 + CEN-1 are covered; the DH-1 box is not.
  expect(coverage.covered_features).toBeGreaterThanOrEqual(2);
  expect(coverage.unheld_features).toBeGreaterThan(0);
  const gap = coverage.unheld.find((row) => row.governing_bylaw === UNHELD_BYLAW);
  expect(gap, `expected a ${UNHELD_BYLAW} gap`).toBeTruthy();
  expect(gap!.feature_count).toBeGreaterThan(0);
});

test("the monitoring endpoint carries the coverage section without going red", async ({
  request,
}) => {
  // A municipality publishes far more by-law areas than any corpus ingests,
  // so incomplete coverage is the steady state. Failing on it would pin this
  // endpoint at 503 and train operators to ignore it.
  const response = await request.get(
    `${E2E_API_URL}/v1/monitoring/corpus-coherence`,
  );
  expect([200, 503]).toContain(response.status());
  const body = (await response.json()) as {
    status: string;
    governing_bylaw_coverage?: { complete: boolean; unheld: unknown[] };
  };

  expect(body.governing_bylaw_coverage, "coverage section missing").toBeTruthy();
  // Whatever the coverage is, it is never the reason the endpoint is red.
  expect(body.status).not.toBe("governing_bylaw_coverage");
});
