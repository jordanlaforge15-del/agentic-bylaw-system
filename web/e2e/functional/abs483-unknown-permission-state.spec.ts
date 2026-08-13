// Functional: ABS-483 — "we could not extract this cell" is its own state, and
// never masquerades as "the bylaw prohibits this use".
//
// The permission vocabulary used to be exactly three values, so a matrix cell
// the parser lost — or one carrying a symbol-font glyph we have no mapping for
// — was forced into not_permitted. This spec pins the split end to end:
//
//   * a MISSING cell            → marker "unknown"      → reason_code unreadable_cell
//   * an UNMAPPED private-use glyph → marker "unknown"  → reason_code unreadable_cell
//   * a PRESENT-but-blank cell  → still "not_permitted" (that IS the bylaw's
//     convention, and must not drift into the new state)
//
// Seeds the matrix (scripts/seed_e2e_unknown_permission.py), runs Phase-2
// enrichment via /v1/_test/profile-permission-tables — which also reports the
// resolved cell's marker — then drives the real retrieval service through
// /v1/_test/lookup-citation for the caller-facing contract.

import type { APIRequestContext } from "@playwright/test";
import { execSync } from "node:child_process";
import * as path from "node:path";

import { E2E_API_URL, expect, test } from "../fixtures/test-env";

const BYLAW_NAME = "Unknown Permission Test By-law";

function runSeeds(): void {
  const repoRoot = path.resolve(__dirname, "..", "..", "..");
  const venvPython = path.join(repoRoot, ".venv", "bin", "python");
  const pgPort = process.env.PG_PORT || "5433";
  const databaseUrl =
    process.env.DATABASE_URL ||
    `postgresql+psycopg://layer1:layer1@localhost:${pgPort}/layer1_test`;
  const env = {
    ...process.env,
    DATABASE_URL: databaseUrl,
    PYTHONPATH: `${path.join(repoRoot, "src")}:${process.env.PYTHONPATH || ""}`,
  };
  execSync(
    `"${venvPython}" "${path.join(repoRoot, "scripts", "seed_e2e_unknown_permission.py")}"`,
    { env, stdio: "inherit" },
  );
}

// Runs semantic enrichment (binds the axes + annotates the markers) and returns
// the addressed cell's resolution for the given (use, zone) pair.
async function resolveCell(
  request: APIRequestContext,
  use: string,
  zone: string,
) {
  const response = await request.post(
    `${E2E_API_URL}/v1/_test/profile-permission-tables`,
    {
      headers: { "Content-Type": "application/json" },
      data: { bylaw_name: BYLAW_NAME, use_name: use, zone },
    },
  );
  expect(
    response.status(),
    `profile-permission-tables failed: ${await response.text()}`,
  ).toBe(200);
  const body = await response.json();
  expect(body.table_count).toBeGreaterThanOrEqual(1);
  return { documentId: body.document_id as number, resolution: body.resolution };
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
  const seeded = await resolveCell(request, "Restaurant use", "DD");
  documentId = seeded.documentId;
});

test("a missing matrix cell resolves to the unknown marker, not not_permitted", async ({
  request,
}) => {
  const { resolution } = await resolveCell(request, "Restaurant use", "DH");
  expect(resolution, "both axes bind, so the pair still resolves").toBeTruthy();
  // Nothing was extracted at this intersection...
  expect(resolution.cell_text ?? null).toBeNull();
  // ...so the marker says exactly that.
  expect(resolution.permission_marker).toBe("unknown");
});

test("an unmapped symbol-font glyph resolves to the unknown marker", async ({
  request,
}) => {
  const { resolution } = await resolveCell(request, "Office use", "DH");
  expect(resolution).toBeTruthy();
  expect(resolution.permission_marker).toBe("unknown");
});

test("a present-but-blank cell is still not_permitted", async ({ request }) => {
  const { resolution } = await resolveCell(request, "Restaurant use", "COR");
  expect(resolution).toBeTruthy();
  expect(resolution.cell_text).toBe("");
  expect(resolution.permission_marker).toBe("not_permitted");
});

test("a missing cell surfaces to the caller as a typed unreadable_cell, never a prohibition", async ({
  request,
}) => {
  const result = await lookupPermittedUse(request, documentId, "Restaurant use", "DH");
  expect(result.permission ?? null).toBeNull();
  expect(result.indeterminate).toBe(true);
  expect(result.reason_code).toBe("unreadable_cell");
  // The reason has to say WHY, so the answer isn't read back as a prohibition.
  expect(result.reason).toContain("does NOT mean the use is prohibited");
  // Still grounded — the caller can go read the table the gap is in.
  expect(result.citation).toBeTruthy();
  expect(result.citation.bylaw_name).toBe(BYLAW_NAME);
});

test("an unmapped glyph cell surfaces as unreadable_cell too", async ({
  request,
}) => {
  const result = await lookupPermittedUse(request, documentId, "Office use", "DH");
  expect(result.indeterminate).toBe(true);
  expect(result.permission ?? null).toBeNull();
  expect(result.reason_code).toBe("unreadable_cell");
});

test("the ordinary markers are untouched by the new state", async ({
  request,
}) => {
  const permitted = await lookupPermittedUse(request, documentId, "Restaurant use", "DD");
  expect(permitted.indeterminate).toBeFalsy();
  expect(permitted.permission).toBe("permitted");

  // The blank-cell convention survives at the caller-facing boundary as well:
  // this is the one "empty" case that is real bylaw content.
  const blank = await lookupPermittedUse(request, documentId, "Restaurant use", "COR");
  expect(blank.indeterminate).toBeFalsy();
  expect(blank.permission).toBe("not_permitted");
});
