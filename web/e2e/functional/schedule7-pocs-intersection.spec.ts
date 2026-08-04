// Functional regression: Schedule 7 Pedestrian-Oriented Commercial Streets
// spatial layer — ABS-349.
//
// Schedule 7 of the Regional Centre Land Use By-Law is a MAP-ONLY schedule:
// the by-law text references it in 23+ fragments (ground-floor uses
// s.38(2)/s.69(d), setbacks, streetwall storeys) but never enumerates the
// streets, so before this layer existed no retrieval call could answer "does
// this lot abut a pedestrian-oriented commercial street". The dev-DB answer
// for 6184 Quinpool Rd flipped on exactly that fact and had to be delivered
// as a two-scenario conditional.
//
// This spec proves the ingested POCS layer is spatially queryable on the real
// Postgres/PostGIS stack:
//
//   beforeAll  -> scripts/seed_e2e_pocs.py ingests the POCS dataset (a
//                 Quinpool Road segment + a control Gottingen Street segment)
//                 through ingest_geo_dataset — populating the PostGIS
//                 `geometry` column, not just geometry_geojson — links it to
//                 the Regional Centre LUB "Schedule 7" fragment, and seeds
//                 geocode-cache rows for "6184 Quinpool Road" (~10 m off the
//                 centreline) and a far-away "500 Nowhere Road" control.
//   probe      -> scripts/inspect_pocs_intersection.py resolves the address
//                 through the geocode cache, buffers the point, and runs the
//                 production `query_features` (ST_Intersects) against the
//                 dataset.
//   assertion  -> 6184 Quinpool Rd's buffered point intersects the Quinpool
//                 segment; the control address intersects nothing.
//
// The retrieval-service overlay-role wiring that would surface this in a full
// answer is a separate ticket, so the inspect script is the test-scaffolding
// seam that lets this spec exercise the layer without expanding the API
// surface (same pattern as inspect_case_metadata.py for lot-facts).

import { execSync } from "node:child_process";
import * as path from "node:path";

import { expect, test } from "../fixtures/test-env";


type ProbeResult = {
  address: string;
  resolved: boolean;
  intersects: boolean;
  segment_count: number;
  streets: string[];
};


function repoRoot(): string {
  return path.resolve(__dirname, "..", "..", "..");
}


function venvPython(): string {
  return path.join(repoRoot(), ".venv", "bin", "python");
}


function scriptEnv(): NodeJS.ProcessEnv {
  // ABS-207: honor PG_PORT so the parallel-worktree case lands in the right
  // Postgres; fall back to the standard layer1_test URL otherwise.
  const pgPort = process.env.PG_PORT || "5433";
  const databaseUrl =
    process.env.DATABASE_URL ||
    `postgresql+psycopg://layer1:layer1@localhost:${pgPort}/layer1_test`;
  return {
    ...process.env,
    DATABASE_URL: databaseUrl,
    PYTHONPATH: `${path.join(repoRoot(), "src")}:${process.env.PYTHONPATH || ""}`,
  };
}


function runSeed(): void {
  const seed = path.join(repoRoot(), "scripts", "seed_e2e_pocs.py");
  execSync(`"${venvPython()}" "${seed}"`, { env: scriptEnv(), stdio: "inherit" });
}


function probe(address: string): ProbeResult {
  const inspect = path.join(repoRoot(), "scripts", "inspect_pocs_intersection.py");
  // stdio "pipe" for stdout; inherit stderr so a Python traceback surfaces in
  // the Playwright log if the probe fails.
  const output = execSync(
    `"${venvPython()}" "${inspect}" --address ${JSON.stringify(address)}`,
    { env: scriptEnv(), stdio: ["ignore", "pipe", "inherit"] },
  );
  return JSON.parse(output.toString("utf-8")) as ProbeResult;
}


test.beforeAll(() => {
  runSeed();
});


test("6184 Quinpool Rd abuts a Schedule 7 pedestrian-oriented commercial street", () => {
  const result = probe("6184 Quinpool Road");

  expect(
    result.resolved,
    "geocode cache didn't resolve the seeded address",
  ).toBe(true);
  // The buffered point intersects the Quinpool corridor. This is the fact the
  // dev-DB answer couldn't establish before the layer existed.
  expect(result.intersects).toBe(true);
  expect(result.segment_count).toBeGreaterThanOrEqual(1);
  expect(result.streets).toContain("Quinpool Road");
});


test("an address off every designated corridor abuts no Schedule 7 street", () => {
  const result = probe("500 Nowhere Road");

  expect(result.resolved).toBe(true);
  // Negative control: the buffered point matches nothing, so the layer does
  // not spuriously report a POCS designation for a non-POCS address.
  expect(result.intersects).toBe(false);
  expect(result.segment_count).toBe(0);
  expect(result.streets).toEqual([]);
});
