// Functional: ABS-283 — Mainland LUB permitted-use extraction (not symbol-dot)
// + classifier false-positive fix.
//
// The Halifax Mainland LUB does NOT use the Regional Centre symbol-font ●
// permission-matrix convention; it lists permitted uses as prose under each
// zone's section. It also carries section/amendment-history tables that the
// structural classifier used to false-positive as a permission matrix.
//
// Seeds a Mainland-shaped doc (scripts/seed_e2e_mainland_permitted_use.py): a
// prose permitted-use section + an amendment table (section-number rows,
// amendment-date + council-case columns). Runs Phase-2 enrichment via
// /v1/_test/profile-permission-tables, then drives the real service through
// /v1/_test/lookup-citation.
//
// Asserts:
//   AC1 — enrichment classifies 0 permission_matrix tables (the amendment table
//         is excluded), so the marker backfill stamps no garbage cells.
//   AC2 — a Mainland (use, zone) query resolves via the section-prose path to a
//         grounded permitted verdict, and an absent use to not_permitted.

import type { APIRequestContext } from "@playwright/test";
import { execSync } from "node:child_process";
import * as path from "node:path";

import { E2E_API_URL, expect, test } from "../fixtures/test-env";
import { resolveDatabaseUrl } from "../helpers/database-url";

const BYLAW_NAME = "Mainland Permitted Use Test By-law";

function runSeeds(): void {
  const repoRoot = path.resolve(__dirname, "..", "..", "..");
  const venvPython = path.join(repoRoot, ".venv", "bin", "python");
  const databaseUrl = resolveDatabaseUrl();
  const env = {
    ...process.env,
    DATABASE_URL: databaseUrl,
    PYTHONPATH: `${path.join(repoRoot, "src")}:${process.env.PYTHONPATH || ""}`,
  };
  execSync(
    `"${venvPython}" "${path.join(repoRoot, "scripts", "seed_e2e_mainland_permitted_use.py")}"`,
    { env, stdio: "inherit" },
  );
}

async function enrichMainland(
  request: APIRequestContext,
): Promise<{ documentId: number; matrixCount: number }> {
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
  return {
    documentId: body.document_id as number,
    matrixCount: body.table_count as number,
  };
}

async function lookupPermittedUse(
  request: APIRequestContext,
  documentId: number,
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
  expect(
    response.status(),
    `lookup-citation failed: ${await response.text()}`,
  ).toBe(200);
  const body = await response.json();
  expect(body.permitted_use, "expected a permitted_use envelope").toBeTruthy();
  return body.permitted_use;
}

let documentId: number;
let matrixCount: number;

test.beforeAll(async ({ request }) => {
  runSeeds();
  ({ documentId, matrixCount } = await enrichMainland(request));
});

test("AC1: the amendment/section table is not classified as a permission matrix", () => {
  // 0 permission_matrix profiles on the Mainland doc — the false positive is
  // gone, so the marker backfill stamps no garbage not_permitted cells.
  expect(matrixCount).toBe(0);
});

test("AC2: a Mainland (use, zone) query resolves to permitted via section prose", async ({
  request,
}) => {
  const result = await lookupPermittedUse(
    request,
    documentId,
    "Single Unit Dwelling",
    "ICH",
  );
  expect(result.indeterminate).toBeFalsy();
  expect(result.permission).toBe("permitted");
  expect(result.citation).toBeTruthy();
  expect(result.citation.bylaw_name).toBe(BYLAW_NAME);
});

test("AC2: a use absent from the closed Mainland list resolves to not_permitted", async ({
  request,
}) => {
  const result = await lookupPermittedUse(request, documentId, "Restaurant use", "ICH");
  expect(result.indeterminate).toBeFalsy();
  expect(result.permission).toBe("not_permitted");
});
