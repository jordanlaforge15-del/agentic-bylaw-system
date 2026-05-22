// Functional regression: ABS-66 — the chat agent was hallucinating a
// bylaw name from the bare BYLAW_ID integer (it called HRM BYLAW_ID 9
// "Halifax Peninsula Land Use By-law" when 9 is actually Halifax
// Mainland). The fix moves the integer → name mapping into the
// dataset's YAML as a per-publisher lookup table, so the ingest path
// resolves bylaw_area_code + bylaw_area_name onto every feature's
// canonical_attributes_json before retrieval ever sees it.
//
// This spec drives the regression end-to-end through the running
// ingest pipeline:
//
//   beforeAll  -> scripts/seed_e2e_zoning.py loads
//                 src/layer1/datasets/halifax_zoning.yaml verbatim,
//                 swaps source_url for a tiny fixture GeoJSON with one
//                 polygon (BYLAW_ID=9, ZONE=R-1), and runs
//                 ingest_geo_dataset against layer1_test.
//   test       -> scripts/inspect_zoning_canonical.py reads back the
//                 ingested feature's canonical_attributes_json from
//                 the test DB and prints it as JSON.
//   assertion  -> the JSON carries the resolved code/name pair, not
//                 just the raw integer the agent used to misread.
//
// We don't go through chat SSE here because the mock dispatcher emits
// a hardcoded reply — the value the agent would actually see is the
// canonical_attributes payload, which is what this spec pins.

import { execSync } from "node:child_process";
import * as path from "node:path";

import { expect, test } from "../fixtures/test-env";


const FIXTURE_GLOBALID = "e2e-zoning-abs66-mainland";


function repoRoot(): string {
  return path.resolve(__dirname, "..", "..", "..");
}


function venvPython(): string {
  return path.join(repoRoot(), ".venv", "bin", "python");
}


function pythonEnv(): NodeJS.ProcessEnv {
  const databaseUrl =
    process.env.DATABASE_URL ||
    "postgresql+psycopg://layer1:layer1@localhost:5432/layer1_test";
  return {
    ...process.env,
    DATABASE_URL: databaseUrl,
    PYTHONPATH: `${path.join(repoRoot(), "src")}:${process.env.PYTHONPATH || ""}`,
  };
}


function runSeed(): void {
  const seed = path.join(repoRoot(), "scripts", "seed_e2e_zoning.py");
  execSync(`"${venvPython()}" "${seed}"`, {
    env: pythonEnv(),
    stdio: "inherit",
  });
}


function readCanonicalAttributes(globalid: string): Record<string, unknown> {
  const inspect = path.join(repoRoot(), "scripts", "inspect_zoning_canonical.py");
  const output = execSync(
    `"${venvPython()}" "${inspect}" --globalid "${globalid}"`,
    {
      env: pythonEnv(),
      stdio: ["ignore", "pipe", "inherit"],
    },
  );
  return JSON.parse(output.toString("utf-8")) as Record<string, unknown>;
}


test.beforeAll(() => {
  runSeed();
});


test("zoning ingest resolves BYLAW_ID into bylaw_area_code + bylaw_area_name", () => {
  const canonical = readCanonicalAttributes(FIXTURE_GLOBALID);

  // Raw integer still surfaces for downstream filters that key on it
  // (e.g. RetrievalService.attribute_tag_filter from ABS-45).
  expect(canonical.bylaw_area_id).toBe(9);

  // The lookup mapping in halifax_zoning.yaml resolves BYLAW_ID=9 to
  // Halifax Mainland — NOT Halifax Peninsula (BYLAW_ID=10), which is
  // the specific hallucination the issue reported.
  expect(canonical.bylaw_area_code).toBe("hrm:HMAIN");
  expect(canonical.bylaw_area_name).toBe("Halifax Mainland Land Use By-law");

  // Sanity: the rest of the canonical attributes still parse so the
  // lookup path didn't trample the existing fields.
  expect(canonical.zone_code).toBe("R-1");
});
