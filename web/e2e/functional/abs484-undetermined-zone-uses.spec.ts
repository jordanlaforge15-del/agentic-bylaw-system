// Functional: ABS-484 — UNKNOWN absorbs through the zone profile. A use whose
// matrix cell we could not read is reported as undetermined, never as a
// prohibition, and nothing is cited in support of it.
//
// Before this, _build_zone_uses_from_matrix returned before the prose fallback
// and stamped confidence 0.9 over the whole block, so a hole in the column was
// served to the user as an authoritative "not permitted" with a citation beside
// it. The seeded matrix (scripts/seed_e2e_undetermined_uses.py) carries three
// columns that pin the three outcomes:
//
//   * DD  — one hole, no prose → the use lands in uses.undetermined
//   * DH  — the same hole, but a P/N prose row states the permission → the
//           prose fallback resolves it into permitted, with the prose citation
//   * COR — header only, every data cell missing → nothing determinate, so the
//           profile claims no confidence and cites nothing for uses
//
// Drives the real retrieval service through /v1/_test/zone-profile (the compact
// projection the model actually reads) and /v1/_test/bylaw-query use_check.

import type { APIRequestContext } from "@playwright/test";
import { execSync } from "node:child_process";
import * as path from "node:path";

import { E2E_API_URL, expect, test } from "../fixtures/test-env";
import { resolveDatabaseUrl } from "../helpers/database-url";

const BYLAW_NAME = "Undetermined Uses Test By-law";

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
    `"${venvPython}" "${path.join(repoRoot, "scripts", "seed_e2e_undetermined_uses.py")}"`,
    { env, stdio: "inherit" },
  );
}

// Runs semantic enrichment so the matrix axes are bound, and hands back the
// seeded document id every later call scopes to.
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

test("a use whose cell was lost is undetermined, not prohibited", async ({
  request,
}) => {
  const profile = await zoneProfile(request, "DD");
  const uses = profile.uses;

  expect(uses.undetermined).toContain("Restaurant use");
  // The whole point: it must NOT be reported as a prohibition, and must not be
  // silently claimed as permitted either.
  expect(uses.not_permitted ?? []).not.toContain("Restaurant use");
  expect(uses.permitted ?? []).not.toContain("Restaurant use");

  // The cells we DID read are unaffected — including the blank-cell
  // convention, which is real bylaw content.
  expect(uses.permitted).toContain("Office use");
  expect(uses.not_permitted).toContain("Multi-unit dwelling use");
});

test("the undetermined list ships the language the agent has to relay", async ({
  request,
}) => {
  const profile = await zoneProfile(request, "DD");
  const instruction: string = profile.uses.instruction;

  expect(instruction, "an undetermined list must carry its instruction").toBeTruthy();
  expect(instruction).toContain("not determinable");
  // ...and must explicitly forbid the two collapses.
  expect(instruction).toContain("prohibited");
  expect(instruction).toContain("permitted");
});

test("the prose fallback resolves an undetermined use and cites the prose", async ({
  request,
}) => {
  const profile = await zoneProfile(request, "DH");
  const uses = profile.uses;

  // DH's cell is just as lost as DD's, but the bylaw states the permission in
  // prose — so the matrix path may not short-circuit on the gap.
  expect(uses.permitted).toContain("Restaurant use");
  expect(uses.undetermined ?? []).not.toContain("Restaurant use");

  const usesCitations = (profile.citations ?? []).filter((c: { backs?: string[] }) =>
    (c.backs ?? []).includes("uses"),
  );
  const paths = usesCitations.map((c: { citation_path?: string }) => c.citation_path);
  expect(paths, "the prose fragment backs the resolved use").toContain(
    "Table 1B > DH > Use Permissions",
  );
  // Taking in a prose reading can only ever lower the block's confidence —
  // it is the weaker of the two claims, never the stronger.
  expect(profile.confidence.uses).toBeLessThanOrEqual(0.9);
});

test("a column of nothing but holes claims no confidence and cites nothing", async ({
  request,
}) => {
  const profile = await zoneProfile(request, "COR");
  const uses = profile.uses;

  expect(uses.undetermined).toEqual(
    expect.arrayContaining([
      "Restaurant use",
      "Office use",
      "Multi-unit dwelling use",
      "Daycare use",
    ]),
  );
  expect(uses.permitted ?? []).toEqual([]);
  expect(uses.not_permitted ?? []).toEqual([]);
  expect(uses.conditional ?? []).toEqual([]);

  // Nothing was read, so nothing is claimed: no 0.9, and no citation that
  // could be relayed as evidence for a use verdict.
  expect(profile.confidence?.uses).toBeUndefined();
  const usesCitations = (profile.citations ?? []).filter((c: { backs?: string[] }) =>
    (c.backs ?? []).includes("uses"),
  );
  expect(usesCitations).toEqual([]);
});

test("the use_check intent inherits the undetermined split", async ({ request }) => {
  const response = await request.post(`${E2E_API_URL}/v1/_test/bylaw-query`, {
    headers: { "Content-Type": "application/json" },
    data: { intent: "use_check", zone: "DD", document_id: documentId },
  });
  expect(response.status(), `bylaw-query failed: ${await response.text()}`).toBe(200);
  const body = await response.json();
  const uses = body.compact.zone_profile.uses;

  expect(uses.undetermined).toContain("Restaurant use");
  expect(uses.not_permitted ?? []).not.toContain("Restaurant use");
  expect(uses.instruction).toContain("not determinable");
});
