// Functional: ABS-284 — profile-driven enrichment classification.
//
// Enrichment classification (which tables are permission matrices, which glyphs
// mean "permitted") now reads the bylaw's convention from the active profile
// instead of hardcoding Regional-Centre assumptions in shared code. This spec
// proves the profile threads end-to-end through the real FastAPI enrichment
// stack: the same Mainland-shaped doc is enriched under an explicit
// `section_indexed` profile and yields 0 permission_matrix tables, while the
// section-prose permitted-use path still resolves a grounded verdict.
//
// Reuses the ABS-283 Mainland seed (a prose permitted-use section + an
// amendment/section-history table). The endpoint accepts an optional
// `permission_encoding` (ABS-284) that builds a ParsingProfile passed to
// enrichment.
//
// Asserts:
//   AC1/AC3 — under a `section_indexed` profile, 0 tables classify as
//             permission_matrix (the amendment table is never a matrix, and the
//             section×zone shape is not treated as one either).
//   AC1     — the section-prose (use, zone) lookup still resolves to permitted.

import type { APIRequestContext } from "@playwright/test";
import { execSync } from "node:child_process";
import * as path from "node:path";

import { E2E_API_URL, expect, test } from "../fixtures/test-env";

const BYLAW_NAME = "Mainland Permitted Use Test By-law";

function runSeeds(): void {
  const repoRoot = path.resolve(__dirname, "..", "..", "..");
  const venvPython = path.join(repoRoot, ".venv", "bin", "python");
  const pgPort = process.env.PG_PORT || "5432";
  const databaseUrl =
    process.env.DATABASE_URL ||
    `postgresql+psycopg://layer1:layer1@localhost:${pgPort}/layer1_test`;
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

async function enrichWithEncoding(
  request: APIRequestContext,
  permissionEncoding: string,
): Promise<{ documentId: number; matrixCount: number }> {
  const response = await request.post(
    `${E2E_API_URL}/v1/_test/profile-permission-tables`,
    {
      headers: { "Content-Type": "application/json" },
      data: { bylaw_name: BYLAW_NAME, permission_encoding: permissionEncoding },
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
  ({ documentId, matrixCount } = await enrichWithEncoding(request, "section_indexed"));
});

test("AC1/AC3: a section_indexed profile yields 0 permission_matrix tables", () => {
  expect(matrixCount).toBe(0);
});

test("AC1: the section-prose permitted-use path resolves under the profile", async ({
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
  expect(result.citation.bylaw_name).toBe(BYLAW_NAME);
});
