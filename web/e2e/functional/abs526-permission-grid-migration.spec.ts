// Functional: ABS-526 — the ABS-520 repair reaches a corpus nobody re-ingests.
//
// ABS-520 shipped as code plus a data migration, and only the code half had a
// delivery mechanism. `scripts/backfill_permission_grid.py` was run by hand
// against dev; production kept its ragged grid through the release that
// "fixed" it, and went on telling users "the permission could not be
// extracted" where the by-law prints a blank cell — which in the Regional
// Centre LUB *is* the prohibition. Every test was green throughout, because
// every test ran against a corpus enrichment had densified at ingest.
//
// So the thing under test here is not the densifier (abs520-ragged-permission
// -grid.spec.ts owns that). It is the delivery: `alembic upgrade head`, which
// every deploy runs, repairing a corpus that was ingested before the repair
// existed and will never be re-ingested.
//
// The state production was in is reconstructed exactly:
//
//   1. seed the ragged matrix          — the parser's output, blanks dropped
//   2. enrich it                       — classified as a permission matrix
//   3. strip the materialized cells    — leaving a profiled corpus with a
//                                        ragged grid, and nothing else changed
//   4. assert (Townhouse, ER-2) reads `unknown`   <- the production symptom
//   5. rewind the stamp, `alembic upgrade head`   <- what a deploy does
//   6. assert it reads `not_permitted` with a citation
//
// Step 5 only rewinds the alembic *pointer*; it never runs a downgrade, so a
// spec running in parallel keeps its cells. The migration is idempotent, and
// only ever adds cells the geometry vouches for, so re-running it across the
// whole e2e database is a no-op for every other document in it.

import type { APIRequestContext } from "@playwright/test";
import { execSync } from "node:child_process";
import * as path from "node:path";

import { E2E_API_URL, expect, test } from "../fixtures/test-env";
import { resolveDatabaseUrl } from "../helpers/database-url";

// A document of this spec's own: step 3 deletes cells, and Playwright runs
// spec files in parallel — doing that to ABS-520's fixture mid-run would fail
// that spec for a reason that has nothing to do with its subject.
const BYLAW_NAME = "Migrated Grid Test By-law";
const FILE_HASH = "e2e-abs526-permission-grid-migration-1";
const CAPTION = "Table 1M: Permitted uses by zone — migrated grid";

// The revision before the backfill. Rewinding the stamp to it makes
// `upgrade head` replay the migration against the corpus as it stands now.
const PRIOR_REVISION = "0026_drop_parcel_zone_code";

const REPO_ROOT = path.resolve(__dirname, "..", "..", "..");

function repoEnv(): NodeJS.ProcessEnv {
  return {
    ...process.env,
    DATABASE_URL: resolveDatabaseUrl(),
    PYTHONPATH: `${path.join(REPO_ROOT, "src")}:${process.env.PYTHONPATH || ""}`,
  };
}

function seedRagged(): void {
  execSync(
    [
      `"${path.join(REPO_ROOT, ".venv", "bin", "python")}"`,
      `"${path.join(REPO_ROOT, "scripts", "seed_e2e_ragged_permission_grid.py")}"`,
      `--file-hash "${FILE_HASH}"`,
      `--bylaw-name "${BYLAW_NAME}"`,
      `--caption "${CAPTION}"`,
    ].join(" "),
    { env: repoEnv(), stdio: "inherit" },
  );
}

// Deletes the cells enrichment materialized, and nothing else: the profile,
// the axis bindings and every cell the parser stored survive. That is a corpus
// ingested before the repair existed — production's, until this migration.
function stripGridFills(): void {
  execSync(
    [
      `"${path.join(REPO_ROOT, ".venv", "bin", "python")}"`,
      `"${path.join(REPO_ROOT, "scripts", "e2e_strip_permission_grid_fills.py")}"`,
      `--file-hash "${FILE_HASH}"`,
    ].join(" "),
    { env: repoEnv(), stdio: "inherit" },
  );
}

function alembic(...args: string[]): void {
  execSync(
    [
      `"${path.join(REPO_ROOT, ".venv", "bin", "alembic")}"`,
      `-c "${path.join(REPO_ROOT, "alembic.ini")}"`,
      ...args,
    ].join(" "),
    { env: repoEnv(), cwd: REPO_ROOT, stdio: "inherit" },
  );
}

async function enrich(request: APIRequestContext): Promise<number> {
  const response = await request.post(
    `${E2E_API_URL}/v1/_test/profile-permission-tables`,
    {
      headers: { "Content-Type": "application/json" },
      data: { bylaw_name: BYLAW_NAME },
    },
  );
  expect(
    response.status(),
    `profile-permission-tables failed: ${await response.text()}`,
  ).toBe(200);
  const body = await response.json();
  expect(body.table_count, "the seeded matrix must classify").toBeGreaterThanOrEqual(
    1,
  );
  return body.document_id as number;
}

async function lookupPermittedUse(
  request: APIRequestContext,
  use: string,
  zone: string,
) {
  const response = await request.post(`${E2E_API_URL}/v1/_test/lookup-citation`, {
    headers: { "Content-Type": "application/json" },
    data: {
      structured: { kind: "permitted_use", use, zone },
      document_id: documentId,
    },
  });
  expect(response.status(), `lookup-citation failed: ${await response.text()}`).toBe(
    200,
  );
  const body = await response.json();
  expect(body.permitted_use, "expected a permitted_use envelope").toBeTruthy();
  return body.permitted_use;
}

let documentId: number;

// One ordered story, not three independent cases: the "before" reading is only
// meaningful in the window between the strip and the migration.
test.describe.configure({ mode: "serial" });

test.beforeAll(async ({ request }) => {
  seedRagged();
  documentId = await enrich(request);
  // Enrichment densified as it classified. Strip those fills back out: a corpus
  // profiled at ingest but never repaired is exactly what production had.
  stripGridFills();
});

test("a corpus ingested before the repair still serves the prohibition as unreadable", async ({
  request,
}) => {
  const result = await lookupPermittedUse(request, "Townhouse dwelling use", "ER-2");

  expect(result.indeterminate, "the defect ABS-520 filed, still live in prod").toBe(
    true,
  );
  expect(result.reason_code).toBe("unreadable_cell");
  expect(result.permission ?? null).toBeNull();
});

test("alembic upgrade head repairs it, without a re-ingest or a re-parse", async ({
  request,
}) => {
  // What the deploy runs. The stamp rewind replays the backfill and nothing
  // else — no schema statement sits between 0026 and head.
  alembic("stamp", PRIOR_REVISION);
  alembic("upgrade", "head");

  const result = await lookupPermittedUse(request, "Townhouse dwelling use", "ER-2");

  expect(result.permission).toBe("not_permitted");
  expect(result.indeterminate, "a blank cell is an answer, not a gap").toBe(false);
  expect(
    result.citation,
    "the prohibition arrives with the table it was read from",
  ).toBeTruthy();
  expect(result.table_id).toBeTruthy();
});

test("the row the parser actually lost is still refused", async ({ request }) => {
  // The migration must not paper over the genuine extraction gap sitting beside
  // the ragged rows: "Cluster housing use" has no label cell at all, and its
  // dots landed in a y band matching no row. "Backyard suite use" brackets that
  // damage, so its missing cells stay missing — filling them would fabricate a
  // prohibition for a use the by-law permits, which is what ABS-483 forbids.
  const result = await lookupPermittedUse(request, "Backyard suite use", "CH-1");

  expect(result.indeterminate, "the extraction gap survives the migration").toBe(
    true,
  );
  expect(result.reason_code).toBe("unreadable_cell");
  expect(result.permission ?? null).toBeNull();
});
