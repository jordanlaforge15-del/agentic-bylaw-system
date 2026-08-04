// Functional regression: lot_facts centerline-buffer frontage, depth,
// and corner detection — ABS-7 (introduction) + ABS-23 (corner-lot fix).
//
// ABS-7 introduced the centerline-buffer algorithm:
//   frontage = ST_Length(ST_Intersection(parcel_boundary,
//                                        ST_Buffer(centerline_union,
//                                                  buffer_m)))
// and surfaced frontage / depth / corner on case-open.
//
// ABS-23 fixed corner / multi-frontage lots: widened the default buffer
// to 12 m (real HRM parcels are inset 7–15 m from centerlines for the
// road allowance, not zero as the original code assumed) and added
// per-street grouping via STR_NAME so multiple centerline segments of
// the same road collapse to one "street". Corner = ≥2 distinct streets
// contribute; depth is omitted for corner lots.
//
// This spec drives both code paths end-to-end through the running
// FastAPI:
//
//   beforeAll  -> scripts/seed_e2e_parcels.py inserts a 15×30 m mid-block
//                 parcel + east-west centerline ("100 Test Street") AND
//                 a 30×40 m corner parcel + two perpendicular centerlines
//                 with distinct STR_NAME ("200 Corner Street"), plus
//                 geocode-cache rows for both addresses.
//   tests      -> POST /v1/cases for each address. The extractor inside
//                 open_case computes spatial_facts and persists them on
//                 advisor_case.metadata_json.
//   assertion  -> scripts/inspect_case_metadata.py reads that row and
//                 prints the JSON; we assert the spatial_facts shape.
//
// CaseOut intentionally does NOT expose metadata_json over HTTP today
// (it's an internal chat-layer detail — the chat route reads it back
// out and renders <lot_facts> into the system prompt). The inspect
// script is the test-scaffolding seam that lets this spec assert on
// the persisted facts without expanding the API surface.

import { execSync } from "node:child_process";
import * as path from "node:path";

import {
  DEMO_USER_ID,
  E2E_API_URL,
  expect,
  test,
} from "../fixtures/test-env";


// Mid-block expected values: 15 m south edge fully inside the 12 m
// buffer (parcel sits on the centerline — HRM tessellation worst case).
// Each perpendicular side edge contributes ~12 m of "artifact" length
// where it crosses the buffer near the parcel's road-facing corners.
// Total: 15 + 2 × 12 = 39 m. Depth = area / frontage = 450 / 39 ≈ 11.5 m.
const MIDBLOCK_AREA_M2 = 450.0;
const MIDBLOCK_PERIMETER_M = 90.0;
const MIDBLOCK_FRONTAGE_M = 39.0;
const MIDBLOCK_DEPTH_M = 11.5;

// Corner-lot expected values: 30 m south edge + 40 m east edge both
// inside their respective street buffers + ~12 m artifact on each of
// the two non-road-facing perpendicular edges ≈ 94 m total frontage.
// area = 1,200 m². Depth must NOT be surfaced (geometrically undefined
// for multi-frontage lots). street_count = 2.
const CORNER_AREA_M2 = 1200.0;
const CORNER_FRONTAGE_M = 94.0;
const CORNER_FRONTAGE_TOLERANCE_M = 2.0;


type OpenCaseResponseShape = {
  case: { id: number };
  credit_id: number;
};


function runSeed(): void {
  const repoRoot = path.resolve(__dirname, "..", "..", "..");
  const seed = path.join(repoRoot, "scripts", "seed_e2e_parcels.py");
  const venvPython = path.join(repoRoot, ".venv", "bin", "python");
  // ABS-207: honor PG_PORT for the parallel-worktree case.
  const pgPort = process.env.PG_PORT || "5433";
  const databaseUrl =
    process.env.DATABASE_URL ||
    `postgresql+psycopg://layer1:layer1@localhost:${pgPort}/layer1_test`;

  execSync(`"${venvPython}" "${seed}"`, {
    env: {
      ...process.env,
      DATABASE_URL: databaseUrl,
      PYTHONPATH: `${path.join(repoRoot, "src")}:${process.env.PYTHONPATH || ""}`,
    },
    stdio: "inherit",
  });
}


function readCaseMetadata(caseId: number): Record<string, unknown> {
  const repoRoot = path.resolve(__dirname, "..", "..", "..");
  const inspect = path.join(repoRoot, "scripts", "inspect_case_metadata.py");
  const venvPython = path.join(repoRoot, ".venv", "bin", "python");
  // ABS-207: honor PG_PORT for the parallel-worktree case.
  const pgPort = process.env.PG_PORT || "5433";
  const databaseUrl =
    process.env.DATABASE_URL ||
    `postgresql+psycopg://layer1:layer1@localhost:${pgPort}/layer1_test`;

  // stdio "pipe" so we can capture stdout; inherit stderr so a Python
  // traceback surfaces in the Playwright log if the inspect step fails.
  const output = execSync(
    `"${venvPython}" "${inspect}" --case-id ${caseId}`,
    {
      env: {
        ...process.env,
        DATABASE_URL: databaseUrl,
        PYTHONPATH: `${path.join(repoRoot, "src")}:${
          process.env.PYTHONPATH || ""
        }`,
      },
      stdio: ["ignore", "pipe", "inherit"],
    },
  );
  return JSON.parse(output.toString("utf-8")) as Record<string, unknown>;
}


async function openCaseAndReadFacts(
  request: import("@playwright/test").APIRequestContext,
  baseAnchorLabel: string,
): Promise<Record<string, unknown>> {
  // Unique anchor suffix per run so parallel workers (and re-runs
  // inside the 30-day case-match window) don't collide. The seeded
  // geocode-cache row keys on the literal address; appending a per-run
  // suffix changes the anchor_label but the extractor still resolves
  // to the seeded geometry via the regex pattern that pulls
  // "<number> <street>" out of any longer string. (See
  // ``layer2.retrieval.location._CIVIC_PATTERN``.)
  const anchorSuffix = `${Date.now()}-${Math.random()
    .toString(36)
    .slice(2, 8)}`;
  const anchorLabel = `${baseAnchorLabel} #${anchorSuffix}`;

  const openRes = await request.post(`${E2E_API_URL}/v1/cases`, {
    headers: { "X-Test-User-Id": DEMO_USER_ID },
    data: {
      anchor_label: anchorLabel,
      anchor_kind: "address",
      tier: "standard",
    },
  });
  expect(
    openRes.status(),
    `open_case failed: ${openRes.status()} ${await openRes.text()}`,
  ).toBe(200);
  const openBody = (await openRes.json()) as OpenCaseResponseShape;
  const caseId = openBody.case.id;

  const metadata = readCaseMetadata(caseId);
  const facts = metadata.spatial_facts as Record<string, unknown> | undefined;
  expect(
    facts,
    `case ${caseId}: metadata_json.spatial_facts missing — extractor didn't run`,
  ).toBeDefined();
  return facts!;
}


test.beforeAll(() => {
  runSeed();
});


test("mid-block lot returns single-street frontage + depth", async ({
  request,
}) => {
  const facts = await openCaseAndReadFacts(request, "100 Test Street");

  expect(facts.status).toBe("ok");
  expect(facts.method).toBe("centerline_buffer");
  expect(facts.pid).toBe("E2E00001");
  expect(facts.anchor_source).toBe("e2e_seed");

  expect(facts.area_m2).toBeCloseTo(MIDBLOCK_AREA_M2, 1);
  expect(facts.perimeter_m).toBeCloseTo(MIDBLOCK_PERIMETER_M, 1);
  // Centerline-buffer frontage with the 12 m default buffer.
  expect(facts.frontage_m).toBeGreaterThan(MIDBLOCK_FRONTAGE_M - 0.5);
  expect(facts.frontage_m).toBeLessThan(MIDBLOCK_FRONTAGE_M + 0.5);
  expect(facts.depth_m).toBeCloseTo(MIDBLOCK_DEPTH_M, 0); // ±0.5 m
  expect(facts.corner).toBe(false);
  expect(facts.street_count).toBe(1);

  // Frontage is well above the 5%-of-perimeter floor (39 / 90 ≈ 43%)
  // and street_count is below the irregular-frontage threshold (3+) →
  // confidence stays at 1.0. Drift here is the early-warning signal
  // for a regression on the confidence-policy code in extractor.py.
  expect(facts.confidence).toBe(1.0);
});


test("ABS-23: corner lot returns frontage spanning two streets, omits depth", async ({
  request,
}) => {
  // 200 Corner Street: 30 × 40 m parcel at the intersection of two
  // perpendicular centerlines named "CORNER" and "CROSS". Pre-ABS-23
  // this case returned corner=false and a bogus depth because the 8 m
  // buffer reached at most one centerline. After ABS-23: corner=true,
  // street_count=2, depth omitted, frontage sums both edges.
  const facts = await openCaseAndReadFacts(request, "200 Corner Street");

  expect(facts.status).toBe("ok");
  expect(facts.pid).toBe("E2E00002");
  expect(facts.area_m2).toBeCloseTo(CORNER_AREA_M2, 1);

  expect(facts.street_count).toBe(2);
  expect(facts.corner).toBe(true);

  // 30 + 40 m main edges + ~12 m artifact on each of the two
  // perpendicular non-frontage edges ≈ 94 m. Tolerance covers
  // projection precision (~0.5 m at Halifax latitudes).
  expect(facts.frontage_m).toBeGreaterThan(
    CORNER_FRONTAGE_M - CORNER_FRONTAGE_TOLERANCE_M,
  );
  expect(facts.frontage_m).toBeLessThan(
    CORNER_FRONTAGE_M + CORNER_FRONTAGE_TOLERANCE_M,
  );

  // Depth is geometrically undefined for multi-frontage parcels —
  // omitted from the persisted facts entirely. A regression that
  // re-introduces `area / frontage` for corner lots will fail this.
  expect(facts.depth_m).toBeUndefined();

  // 2 streets is still "normal" residential corner; confidence stays
  // at 1.0. (3+ streets → irregular → 0.7; 4+ → city-block → suppress.)
  expect(facts.confidence).toBe(1.0);
});
