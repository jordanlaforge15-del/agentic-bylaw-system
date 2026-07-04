// Functional: ABS-279 — Structured permitted-use retrieval. A (use, zone) pair
// resolves to its Table 1A permission-matrix cell through the lookup_citation
// structured envelope ({kind: "permitted_use"}), returning a typed permission
// plus, for a conditional cell, the footnote ordinal + condition text.
//
// Seeds a permission matrix (scripts/seed_e2e_permitted_use.py) with permitted /
// conditional / blank cells + a footnote fragment, runs Phase-2 enrichment via
// /v1/_test/profile-permission-tables to bind the axes, then drives the real
// service through /v1/_test/lookup-citation for each acceptance criterion.

import type { APIRequestContext } from "@playwright/test";
import { execSync } from "node:child_process";
import * as path from "node:path";

import { E2E_API_URL, expect, test } from "../fixtures/test-env";

const BYLAW_NAME = "Permitted Use Test By-law";

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
    `"${venvPython}" "${path.join(repoRoot, "scripts", "seed_e2e_permitted_use.py")}"`,
    { env, stdio: "inherit" },
  );
}

async function enrichAndGetDocumentId(
  request: APIRequestContext,
): Promise<number> {
  // Runs semantic enrichment (binds the matrix axes) and returns the doc id.
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
  expect(body.table_count).toBeGreaterThanOrEqual(1);
  return body.document_id as number;
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

test.beforeAll(async ({ request }) => {
  runSeeds();
  documentId = await enrichAndGetDocumentId(request);
});

test("AC1: a permitted cell resolves to permission=permitted with a citation", async ({
  request,
}) => {
  const result = await lookupPermittedUse(request, documentId, "Restaurant use", "DD");
  expect(result.indeterminate).toBeFalsy();
  expect(result.permission).toBe("permitted");
  expect(result.citation).toBeTruthy();
  expect(result.citation.bylaw_name).toBe(BYLAW_NAME);
});

test("AC2: a conditional cell returns the footnote ordinal + condition text", async ({
  request,
}) => {
  const result = await lookupPermittedUse(request, documentId, "Restaurant use", "DH");
  expect(result.permission).toBe("conditional");
  expect(result.footnote_ordinal).toBe(3);
  expect(result.condition_text).toContain("maximum gross floor area");
});

test("AC3: a blank cell resolves to permission=not_permitted", async ({
  request,
}) => {
  const result = await lookupPermittedUse(request, documentId, "Restaurant use", "COR");
  expect(result.permission).toBe("not_permitted");
});

test("AC4: an unknown use returns a typed indeterminate result with a reason", async ({
  request,
}) => {
  const result = await lookupPermittedUse(request, documentId, "Brewery use", "DD");
  expect(result.indeterminate).toBe(true);
  expect(result.permission ?? null).toBeNull();
  expect(result.reason_code).toBe("unknown_use");
  expect(typeof result.reason).toBe("string");
  expect(result.reason.length).toBeGreaterThan(0);
});

test("AC4: an unknown zone returns a typed indeterminate result with a reason", async ({
  request,
}) => {
  const result = await lookupPermittedUse(request, documentId, "Restaurant use", "ZZ-9");
  expect(result.indeterminate).toBe(true);
  expect(result.reason_code).toBe("unknown_zone");
  expect(result.reason.length).toBeGreaterThan(0);
});

// ABS-351 — near-miss residential use terms resolve to (or suggest) the canonical
// "Multi-unit dwelling use" matrix row instead of returning a blind unknown_use.

for (const nearMiss of ["Multiple-unit dwelling", "multi unit dwelling"]) {
  test(`ABS-351: "${nearMiss}" resolves to the Multi-unit dwelling row`, async ({
    request,
  }) => {
    const result = await lookupPermittedUse(request, documentId, nearMiss, "DD");
    expect(result.indeterminate).toBeFalsy();
    expect(result.permission).toBe("permitted");
    expect(result.citation).toBeTruthy();
  });
}

test('ABS-351: "Dwelling unit" is indeterminate but suggests the canonical row', async ({
  request,
}) => {
  const result = await lookupPermittedUse(request, documentId, "Dwelling unit", "DD");
  expect(result.indeterminate).toBe(true);
  expect(result.reason_code).toBe("unknown_use");
  expect(result.permission ?? null).toBeNull();
  expect(result.suggested_uses).toContain("Multi-unit dwelling use");
});

test("ABS-351: a suggested row, re-issued verbatim, resolves in one more call", async ({
  request,
}) => {
  const first = await lookupPermittedUse(request, documentId, "Dwelling unit", "DD");
  expect(first.suggested_uses.length).toBeGreaterThan(0);
  const second = await lookupPermittedUse(request, documentId, first.suggested_uses[0], "DD");
  expect(second.indeterminate).toBeFalsy();
  expect(second.permission).toBe("permitted");
});
