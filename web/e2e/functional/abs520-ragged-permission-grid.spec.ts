// Functional: ABS-520 — a blank permission-matrix cell survives extraction.
//
// The PDF table parser stores a source_table_cell only where a text run landed.
// In the Regional Centre LUB a cell with no glyph is how the by-law spells "not
// permitted", so Table 1B's townhouse row arrives as
//
//     Townhouse dwelling use | ⑮
//
// with ER-2, ER-1 and CH-1 dropped. Retrieval addressed (Townhouse dwelling
// use, ER-2), found no cell, and reported `unknown` — a flat statutory
// prohibition served to the user as "the permission could not be extracted",
// which reads as "possibly allowed". Golden case TC-026 grades that answer as a
// failure.
//
// The repair materializes those blanks, but only where the cell geometry shows
// the row lost nothing. The seeded matrix
// (scripts/seed_e2e_ragged_permission_grid.py) puts both cases side by side, as
// they are on the real page 48:
//
//   * "Townhouse dwelling use" — ragged, nothing lost   → filled, not_permitted
//   * "Cluster housing use"    — the row the parser LOST (no label; its dots
//     absorbed into the section header below) → the rows around it are refused
//     and keep ABS-483's `undetermined`
//
// Drives the real retrieval service through /v1/_test/lookup-citation (the
// structured permitted_use query from the ABS-517 transcript) and
// /v1/_test/zone-profile (the compact projection the model actually reads).

import type { APIRequestContext } from "@playwright/test";
import { execSync } from "node:child_process";
import * as path from "node:path";

import { E2E_API_URL, expect, test } from "../fixtures/test-env";
import { resolveDatabaseUrl } from "../helpers/database-url";

const BYLAW_NAME = "Ragged Grid Test By-law";

function runSeeds(): void {
  const repoRoot = path.resolve(__dirname, "..", "..", "..");
  const venvPython = path.join(repoRoot, ".venv", "bin", "python");
  const env = {
    ...process.env,
    DATABASE_URL: resolveDatabaseUrl(),
    PYTHONPATH: `${path.join(repoRoot, "src")}:${process.env.PYTHONPATH || ""}`,
  };
  execSync(
    `"${venvPython}" "${path.join(
      repoRoot,
      "scripts",
      "seed_e2e_ragged_permission_grid.py",
    )}"`,
    { env, stdio: "inherit" },
  );
}

// Runs semantic enrichment, which is where the grid is densified, and hands
// back the seeded document id every later call scopes to.
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
  expect(body.table_count, "the seeded matrix must classify").toBeGreaterThanOrEqual(1);
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

async function zoneProfile(request: APIRequestContext, zone: string) {
  const response = await request.post(`${E2E_API_URL}/v1/_test/zone-profile`, {
    headers: { "Content-Type": "application/json" },
    data: { zone, document_id: documentId },
  });
  expect(response.status(), `zone-profile failed: ${await response.text()}`).toBe(200);
  const body = await response.json();
  expect(body.unknown_zone, `${zone} is bound as a matrix column`).toBe(false);
  return body.profile;
}

let documentId: number;

test.beforeAll(async ({ request }) => {
  runSeeds();
  documentId = await enrich(request);
});

test("the dropped blank resolves to the prohibition the by-law prints", async ({
  request,
}) => {
  // The exact query from the ABS-517 transcript, which returned zero citations.
  const result = await lookupPermittedUse(request, "Townhouse dwelling use", "ER-2");

  expect(result.indeterminate, "a blank cell is an answer, not a gap").toBe(false);
  expect(result.permission).toBe("not_permitted");
  expect(result.reason_code ?? null).toBeNull();
  // ...and it is citable: an answer with nothing behind it is not usable.
  expect(result.citation, "the prohibition must carry a citation").toBeTruthy();
  expect(result.table_id).toBeTruthy();
});

test("the markers that were extracted keep their own verdicts", async ({
  request,
}) => {
  const conditional = await lookupPermittedUse(
    request,
    "Townhouse dwelling use",
    "ER-3",
  );
  expect(conditional.permission).toBe("conditional");
  expect(conditional.footnote_ordinal).toBe(15);

  const permitted = await lookupPermittedUse(
    request,
    "Single-unit dwelling use",
    "CH-1",
  );
  expect(permitted.permission).toBe("permitted");
});

test("a row the parser lost is still undetermined, never a prohibition", async ({
  request,
}) => {
  // "Backyard suite use" sits beside the dropped "Cluster housing use" row,
  // whose dots landed in a y band matching no label. The geometry cannot vouch
  // for anything there, so the missing cells stay missing — filling them would
  // fabricate a prohibition, which is exactly what ABS-483 forbids.
  const result = await lookupPermittedUse(request, "Backyard suite use", "CH-1");

  expect(result.indeterminate, "the extraction gap survives the repair").toBe(true);
  expect(result.reason_code).toBe("unreadable_cell");
  expect(result.permission ?? null).toBeNull();
});

test("the zone profile reports the use as prohibited, not undetermined", async ({
  request,
}) => {
  const uses = (await zoneProfile(request, "ER-2")).uses;

  expect(uses.not_permitted).toContain("Townhouse dwelling use");
  expect(uses.undetermined ?? []).not.toContain("Townhouse dwelling use");
  expect(uses.permitted ?? []).not.toContain("Townhouse dwelling use");
});

test("the profile keeps an undetermined list for the rows still lost", async ({
  request,
}) => {
  const uses = (await zoneProfile(request, "CH-1")).uses;

  // The repair must not empty the undetermined list — it exists to carry the
  // gaps that are still real, and its instruction is what the agent relays.
  expect(uses.undetermined).toContain("Backyard suite use");
  expect(uses.not_permitted ?? []).not.toContain("Backyard suite use");
  expect(uses.instruction).toContain("not determinable");
});
