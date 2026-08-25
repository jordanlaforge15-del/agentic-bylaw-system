// Functional: ABS-524 — a use permission reaches the model with the table
// that grants it, in a form the answer can quote.
//
// TC-022 stated the holding "townhouse dwelling use is permitted in ER-3" and
// never named Table 1B, the provision granting it, in 2 of 5 recorded runs.
// Retrieval was never the problem: get_zone_profile supplied the permission and
// carried the Table 1B citation with full provenance in every run, passing and
// failing alike. What reached the model was::
//
//     "citations": [{"citation_path": "Part I > [Table 1B]", "backs": ["uses"]}]
//
// — the quotable string "Table 1B" nowhere in it (the projection dropped
// citation_label whenever a path was present), and nothing anywhere saying the
// permission and the citation belong together. Section 233, read straight off a
// lookup_citation result, was cited in every single run.
//
// So the assertions here are about the payload the model reads, not about the
// grid underneath it:
//
//   * the uses-backing citation carries citation_label AND citation_path
//   * the uses block itself carries cite_as + the attribution instruction
//   * a zone whose uses cite nothing gets neither key — ABS-484's all-holes
//     column must not be pushed into inventing an attribution
//
// The fixture (scripts/seed_e2e_abs524_use_attribution.py) is the first in the
// suite whose permission table has a PARENT FRAGMENT, which is what gives its
// citation a path. Every other matrix fixture is path-less and exercises the
// ABS-409 fallback instead — i.e. it cannot see this defect at all.
//
// Drives the real retrieval service through /v1/_test/zone-profile (the compact
// projection the model actually reads) and /v1/_test/bylaw-query use_check.

import type { APIRequestContext } from "@playwright/test";
import { execSync } from "node:child_process";
import * as path from "node:path";

import { E2E_API_URL, expect, test } from "../fixtures/test-env";
import { resolveDatabaseUrl } from "../helpers/database-url";

const BYLAW_NAME = "Use Attribution Test By-law";
const TABLE_LABEL = "Table 1T";
const TABLE_PATH = "Part I > [Table 1T]";

type CompactCitation = {
  citation_path?: string;
  citation_label?: string;
  pages?: number[];
  backs?: string[];
};

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
      "seed_e2e_abs524_use_attribution.py",
    )}"`,
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

test("the table's citation reaches the model with a label, not just a path", async ({
  request,
}) => {
  const profile = await zoneProfile(request, "ER-3");
  const usesCitations: CompactCitation[] = (profile.citations ?? []).filter(
    (c: CompactCitation) => (c.backs ?? []).includes("uses"),
  );

  expect(usesCitations.length, "the permission must be citable").toBeGreaterThan(0);
  const table = usesCitations.find((c) => c.citation_path === TABLE_PATH);
  expect(table, `expected a uses citation at ${TABLE_PATH}`).toBeTruthy();

  // The defect: a path-bearing citation used to arrive with the label stripped,
  // leaving the model to recover "Table 1T" by parsing "Part I > [Table 1T]".
  expect(table!.citation_label).toBe(TABLE_LABEL);
  // The path survives too — it is what lookup_citation takes.
  expect(table!.citation_path).toBe(TABLE_PATH);
  expect(table!.pages).toEqual([48, 48]);
});

test("the permission and its citation arrive together, not a payload apart", async ({
  request,
}) => {
  const uses = (await zoneProfile(request, "ER-3")).uses;

  // The holding TC-022 stated bare.
  expect(uses.permitted).toContain("Townhouse dwelling use");

  // ...and the provision granting it, bound to the block that carries it
  // rather than only to the citations list at the payload tail.
  const citeAs: CompactCitation[] = uses.cite_as ?? [];
  expect(citeAs.map((c) => c.citation_label)).toContain(TABLE_LABEL);
  expect(citeAs.map((c) => c.citation_path)).toContain(TABLE_PATH);

  const instruction: string = uses.citation_instruction ?? "";
  expect(instruction, "cite_as must ship with what to do about it").toBeTruthy();
  expect(instruction).toContain("citation_label");
  // The two failure modes the instruction names outright: dropping the
  // attribution in a heading, and citing the standards downstream of the use
  // instead of the use itself.
  expect(instruction).toContain("heading");
  expect(instruction).toContain("dimensional standards");
});

test("a prohibition is attributed on the same terms as a permission", async ({
  request,
}) => {
  const uses = (await zoneProfile(request, "ER-2")).uses;

  expect(uses.not_permitted).toContain("Townhouse dwelling use");
  const citeAs: CompactCitation[] = uses.cite_as ?? [];
  expect(citeAs.map((c) => c.citation_label)).toContain(TABLE_LABEL);
});

test("the use_check intent inherits the binding", async ({ request }) => {
  const response = await request.post(`${E2E_API_URL}/v1/_test/bylaw-query`, {
    headers: { "Content-Type": "application/json" },
    data: { intent: "use_check", zone: "ER-3", document_id: documentId },
  });
  expect(response.status(), `bylaw-query failed: ${await response.text()}`).toBe(200);
  const body = await response.json();
  const uses = body.compact.zone_profile.uses;

  expect(uses.permitted).toContain("Townhouse dwelling use");
  expect(
    (uses.cite_as ?? []).map((c: CompactCitation) => c.citation_label),
  ).toContain(TABLE_LABEL);
});

test("a uses block with nothing behind it claims no attribution", async ({
  request,
}) => {
  // COR is the fixture's all-holes column (ABS-484's shape): nothing was read,
  // so nothing is determined and nothing is cited for uses. Shipping an empty
  // cite_as — or an instruction to name a source that isn't there — would push
  // the model to invent one.
  const profile = await zoneProfile(request, "COR");
  const uses = profile.uses;

  expect(uses.undetermined).toContain("Townhouse dwelling use");
  expect(uses.permitted ?? []).toEqual([]);
  expect(uses.not_permitted ?? []).toEqual([]);

  const usesCitations = (profile.citations ?? []).filter((c: CompactCitation) =>
    (c.backs ?? []).includes("uses"),
  );
  expect(usesCitations, "an extraction gap has no authority behind it").toEqual([]);
  expect(uses.cite_as).toBeUndefined();
  expect(uses.citation_instruction).toBeUndefined();
  // ABS-484's own instruction is untouched — this block still has to say the
  // permission is not determinable rather than go quiet.
  expect(uses.instruction).toContain("not determinable");
});
