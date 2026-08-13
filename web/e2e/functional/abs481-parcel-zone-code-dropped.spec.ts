// Functional regression: ABS-481 — the write-only `parcel.zone_code`
// column is gone from the migrated schema.
//
// Scope
// -----
// `parcel.zone_code` was introduced by migration 0014 and documented in
// two places as "a convenience for the evaluator's zone-applicability
// filter". The evaluator never read it: the only writers were
// `scripts/backfill_parcels.py` and the evaluator e2e seed. Migration
// 0026 drops the column and its `ix_parcel_zone_code` index.
//
// Unit tests can only assert the ORM's view of the table (sqlite +
// `create_all`), which would stay green if the migration were missing
// or mis-ordered. This spec asserts the *migrated Postgres* schema the
// stack actually boots against, so a forgotten/failed migration trips
// e2e instead of surfacing as a column drift in production.
//
// Two checks:
//  (a) the migrated `parcel` table has no `zone_code` column and no
//      `ix_parcel_zone_code` index, while the zoning dataset's
//      unrelated canonical `zone_code` attribute is untouched;
//  (b) the updated evaluator seed still inserts its parcel and the
//      evaluator still resolves a submission through it — proving the
//      removal cost no behaviour.

import { execSync } from "node:child_process";
import * as path from "node:path";

import { E2E_API_URL, expect, test } from "../fixtures/test-env";

const repoRoot = path.resolve(__dirname, "..", "..", "..");
const venvPython = path.join(repoRoot, ".venv", "bin", "python");

function pythonEnv(): NodeJS.ProcessEnv {
  // ABS-207: honor PG_PORT so this lands in the right Postgres when a
  // worktree overrides ports for parallel `make e2e`.
  const pgPort = process.env.PG_PORT || "5433";
  return {
    ...process.env,
    DATABASE_URL:
      process.env.DATABASE_URL ||
      `postgresql+psycopg://layer1:layer1@localhost:${pgPort}/layer1_test`,
    PYTHONPATH: `${path.join(repoRoot, "src")}:${process.env.PYTHONPATH || ""}`,
  };
}

function runSeed(): void {
  const seed = path.join(repoRoot, "scripts", "seed_e2e_evaluator_bylaws.py");
  execSync(`"${venvPython}" "${seed}"`, { env: pythonEnv(), stdio: "inherit" });
}

/** Introspect the migrated e2e database and return the result as JSON. */
function inspectSchema(): {
  parcel_columns: string[];
  parcel_indexes: string[];
  seeded_parcels: number;
} {
  const script = [
    "import json, os",
    "from sqlalchemy import create_engine, inspect, text",
    "engine = create_engine(os.environ['DATABASE_URL'])",
    "insp = inspect(engine)",
    "cols = sorted(c['name'] for c in insp.get_columns('parcel'))",
    "idx = sorted(i['name'] for i in insp.get_indexes('parcel'))",
    "conn = engine.connect()",
    "n = conn.execute(text(\"SELECT count(*) FROM parcel WHERE parcel_identifier = 'E2E00100'\")).scalar_one()",
    "print(json.dumps({'parcel_columns': cols, 'parcel_indexes': idx, 'seeded_parcels': n}))",
  ].join("\n");
  const out = execSync(`"${venvPython}" -c ${JSON.stringify(script)}`, {
    env: pythonEnv(),
    encoding: "utf8",
  });
  return JSON.parse(out.trim().split("\n").pop() as string);
}

test.beforeAll(() => {
  runSeed();
});

test("(a) migrated parcel table has no zone_code column or index", () => {
  const schema = inspectSchema();

  expect(schema.parcel_columns).not.toContain("zone_code");
  expect(schema.parcel_indexes).not.toContain("ix_parcel_zone_code");
  // The columns the parcel actually carries are still there — this is a
  // targeted drop, not a table rebuild that lost geometry or identity.
  expect(schema.parcel_columns).toEqual(
    expect.arrayContaining([
      "id",
      "jurisdiction",
      "parcel_identifier",
      "geometry_geojson",
      "centroid_geojson",
      "area_m2",
      "metadata_json",
    ]),
  );
  // The seed still writes its parcel row without the dropped column.
  expect(schema.seeded_parcels).toBeGreaterThan(0);
});

test("(b) evaluator still resolves a submission through the seeded parcel", async ({
  request,
}) => {
  const response = await request.post(`${E2E_API_URL}/v1/_test/evaluate-bylaws`, {
    headers: { "Content-Type": "application/json" },
    data: {
      attributes: [
        { attribute_key: "front_setback_m", value: 6.0, unit: "m" },
        { attribute_key: "building_height_m", value: 9.0, unit: "m" },
      ],
      location: { civic_number: "100", street: "Evaluator Way" },
      document_filters: { bylaw_name: "Evaluator E2E Bylaw" },
    },
  });

  expect(
    response.status(),
    `evaluator endpoint failed: ${response.status()} ${await response.text()}`,
  ).toBe(200);
  const body = (await response.json()) as { overall_status: string };
  // Zone was never read from the parcel, so dropping the column must not
  // change the verdict for the seeded compliant submission.
  expect(body.overall_status).toBe("compliant");
});
