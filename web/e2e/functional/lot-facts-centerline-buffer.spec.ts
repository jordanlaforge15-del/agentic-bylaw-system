// Functional regression: ABS-7 lot_facts Part 2 — centerline-buffer
// frontage, depth, and corner detection.
//
// Pre-fix (ABS-7 Part 1) the case-open extractor surfaced area + perimeter
// only because the shared-edge frontage heuristic collapsed to 0 m on
// HRM's tessellated parcel layer. Part 2 replaces that heuristic with a
// centerline-buffer algorithm:
//   frontage = ST_Length(ST_Intersection(parcel_boundary,
//                                        ST_Buffer(centerline_union,
//                                                  buffer_m)))
// and re-surfaces frontage / depth / corner alongside the existing area.
// See ``src/layer2/spatial/lot_metrics.py`` for the algorithm details.
//
// This spec drives the regression end-to-end through the running FastAPI:
//
//   beforeAll  -> scripts/seed_e2e_parcels.py inserts a 15×30 m parcel,
//                 an east-west centerline along its south edge, and a
//                 geocode-cache row for "100 Test Street".
//   test       -> POST /v1/cases with anchor_label="100 Test Street".
//                 The extractor inside open_case computes spatial_facts
//                 and persists them on advisor_case.metadata_json.
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


// Expected frontage for the seed parcel: the south 15 m edge is fully
// inside the 8 m buffer (parcel sits on the centerline — HRM tessellation
// pattern). Each perpendicular side edge contributes ~buffer_m of
// "artifact" length where it crosses the buffer near the parcel's
// road-facing corners. Total: 15 + 2 × 8 = 31 m. Documented in
// ``lot_metrics.compute_lot_metrics`` and the unit-test fixtures.
const EXPECTED_AREA_M2 = 450.0;
const EXPECTED_PERIMETER_M = 90.0;
const EXPECTED_FRONTAGE_M = 31.0;
const EXPECTED_DEPTH_M = 14.5; // 450 / 31


type OpenCaseResponseShape = {
  case: { id: number };
  credit_id: number;
};


function runSeed(): void {
  const repoRoot = path.resolve(__dirname, "..", "..", "..");
  const seed = path.join(repoRoot, "scripts", "seed_e2e_parcels.py");
  const venvPython = path.join(repoRoot, ".venv", "bin", "python");
  const databaseUrl =
    process.env.DATABASE_URL ||
    "postgresql+psycopg://layer1:layer1@localhost:5432/layer1_test";

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
  const databaseUrl =
    process.env.DATABASE_URL ||
    "postgresql+psycopg://layer1:layer1@localhost:5432/layer1_test";

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


test.beforeAll(() => {
  runSeed();
});


test("open_case for a seeded address persists centerline-buffer lot facts", async ({
  request,
}) => {
  // Unique anchor variation per run so parallel workers (and re-runs
  // inside the 30-day case-match window) don't collide. The seeded
  // geocode-cache row keys on the literal "100 Test Street" address;
  // appending a per-run suffix changes the anchor_label but the
  // extractor still resolves to the seeded geometry via the regex
  // pattern that pulls "100 Test Street" out of any longer string.
  // (See ``layer2.retrieval.location._CIVIC_PATTERN``.)
  const anchorSuffix = `${Date.now()}-${Math.random()
    .toString(36)
    .slice(2, 8)}`;
  const anchorLabel = `100 Test Street #${anchorSuffix}`;

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
  if (!facts) return; // narrow for TS — guarded by the expect above

  // Status / method / provenance.
  expect(facts.status).toBe("ok");
  expect(facts.method).toBe("centerline_buffer");
  expect(facts.pid).toBe("E2E00001");
  expect(facts.anchor_source).toBe("e2e_seed");

  // Geometry: 15 × 30 m parcel at Halifax → 450 m² area, 90 m perimeter.
  expect(facts.area_m2).toBeCloseTo(EXPECTED_AREA_M2, 1);
  expect(facts.perimeter_m).toBeCloseTo(EXPECTED_PERIMETER_M, 1);

  // Centerline-buffer frontage: south edge + perpendicular-edge artifact
  // at each corner. Tolerance covers projection precision (~0.1 m).
  expect(facts.frontage_m).toBeGreaterThan(EXPECTED_FRONTAGE_M - 0.5);
  expect(facts.frontage_m).toBeLessThan(EXPECTED_FRONTAGE_M + 0.5);
  expect(facts.depth_m).toBeCloseTo(EXPECTED_DEPTH_M, 0); // ±0.5 m
  expect(facts.corner).toBe(false);

  // Confidence stays at 1.0 because frontage is well above the 5%-of-
  // perimeter floor (31 / 90 ≈ 34%). A future regression that lets
  // frontage collapse would drop this to 0.7 and the assertion catches it.
  expect(facts.confidence).toBe(1.0);
});
