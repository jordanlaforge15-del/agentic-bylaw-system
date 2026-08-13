// ABS-491: the PostGIS geometry column is derived by one writer, and it
// agrees with the GeoJSON it was derived from.
//
// `external_dataset_feature.geometry` is a denormalization of
// `geometry_geojson`: every ST_Intersects / ST_Contains on the retrieval
// hot path matches against the geometry column, while every other read
// path trusts the JSONB. Before this issue the column was undeclared in
// the ORM and maintained by three independent raw-SQL UPDATEs — so a new
// insert path that forgot one silently produced features that look
// correct in the JSONB and match nothing spatially. sqlite unit tests
// cannot see the column at all, which is why the check lives here.
//
//   beforeAll -> scripts/seed_e2e_parcels.py inserts parcel + centerline
//                features, now routed through
//                layer1.db.geometry.sync_feature_geometry (the single
//                writer) instead of its own copy of the UPDATE.
//   test 1    -> POST /v1/_test/geometry-consistency audits every feature
//                in the e2e database against ST_OrderingEquals with the
//                shape its GeoJSON describes. Zero missing, zero drifted,
//                zero in the wrong SRID — across every seed, not just
//                this spec's.
//   test 2    -> runs the Postgres-gated pytest module against the same
//                database. That is where the audit's *sensitivity* is
//                proven (it seeds drift and asserts the audit names it),
//                which no assertion over a healthy corpus can show.
//
// The address-profile / lot-facts specs cover the spatial read path
// itself; this one covers the invariant those queries depend on.

import { execSync } from "node:child_process";
import * as path from "node:path";

import { E2E_API_URL, expect, test } from "../fixtures/test-env";


type GeometryReport = {
  dialect: string;
  checked: boolean;
  features_total: number;
  features_with_geojson: number;
  features_with_geometry: number;
  missing_geometry: number;
  orphan_geometry: number;
  geometry_mismatch: number;
  srid_mismatch: number;
  ok: boolean;
  sample: { feature_id: number; feature_key: string; status: string }[];
};


const repoRoot = path.resolve(__dirname, "..", "..", "..");
const venvPython = path.join(repoRoot, ".venv", "bin", "python");

// ABS-207: honor PG_PORT for the parallel-worktree case.
const pgPort = process.env.PG_PORT || "5433";
const databaseUrl =
  process.env.DATABASE_URL ||
  `postgresql+psycopg://layer1:layer1@localhost:${pgPort}/layer1_test`;

const pythonEnv = {
  ...process.env,
  DATABASE_URL: databaseUrl,
  PYTHONPATH: `${path.join(repoRoot, "src")}:${process.env.PYTHONPATH || ""}`,
};


test.beforeAll(() => {
  execSync(
    `"${venvPython}" "${path.join(repoRoot, "scripts", "seed_e2e_parcels.py")}"`,
    { env: pythonEnv, stdio: "inherit" },
  );
});


test("every ingested feature's geometry matches its geometry_geojson", async ({
  request,
}) => {
  const res = await request.post(
    `${E2E_API_URL}/v1/_test/geometry-consistency`,
    { data: {} },
  );
  expect(
    res.status(),
    `geometry-consistency failed: ${res.status()} ${await res.text()}`,
  ).toBe(200);
  const report = (await res.json()) as GeometryReport;

  // A sqlite report would be a vacuous pass — assert we really audited
  // PostGIS before trusting the zeroes below.
  expect(report.dialect).toBe("postgresql");
  expect(report.checked).toBe(true);

  // The seeded corpus has spatial features, and every one of them was
  // written through the single writer.
  expect(report.features_with_geojson).toBeGreaterThan(0);
  expect(report.features_with_geometry).toBe(report.features_with_geojson);

  expect(
    report.sample,
    `features whose geometry disagrees with their GeoJSON: ${JSON.stringify(
      report.sample,
    )}`,
  ).toEqual([]);
  expect(report.missing_geometry).toBe(0);
  expect(report.orphan_geometry).toBe(0);
  expect(report.geometry_mismatch).toBe(0);
  expect(report.srid_mismatch).toBe(0);
  expect(report.ok).toBe(true);
});


test("the consistency check detects drift it is pointed at", () => {
  // Postgres-only pytest module: seeds a feature with no geometry and
  // asserts the audit reports `missing_geometry`, overwrites a geometry
  // with a different polygon and asserts `geometry_mismatch`, then
  // repairs via `resync=True`. Runs against this stack's database so the
  // green assertion above is backed by a check that can actually fail.
  execSync(
    `"${venvPython}" -m pytest ` +
      `"${path.join(repoRoot, "tests", "test_feature_geometry_consistency_pg.py")}" ` +
      `-q --no-header -p no:cacheprovider`,
    { cwd: repoRoot, env: pythonEnv, stdio: "inherit" },
  );
});
