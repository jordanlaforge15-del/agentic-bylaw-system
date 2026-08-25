// Functional: ABS-523 — every condition on a permission cell reaches the model,
// and a clause whose path parent names nothing still arrives with its stem.
//
// TC-023 asked whether twelve units were achievable at 6363 Summit Street
// (ER-3, ~286 m²). The advisor said "twelve units is not achievable under the
// current zoning" and routed the developer to a rezoning. The by-law permits
// it twice over — an internal conversion (s.63) and a rear addition (s.233(3))
// — and the 5,512-character answer mentioned neither.
//
// Two defects, one on each end of the same fact's path to the reader:
//
//   1. Table 1B's (ER-3, Multi-unit dwelling use) cell reads "⑮ ㉒". Enrichment
//      kept the first marker. ⑮ is a Halifax Grain Elevator carve-out
//      irrelevant to the address; ㉒ is the only statement in the corpus of how
//      the ER-3 unit cap interacts with the routes that exceed it. So
//      get_zone_profile — the case-open shortcut the agent is told to call
//      first — reported a grain elevator and no route.
//
//   2. s.233(3)'s stem was never given a citation_path, so its clauses read
//      "Part V > 233 > [An addition ... to contain] > (b)" with nothing standing
//      at that segment. lookup_citation("Part V > 233") returned the width and
//      depth clauses and stopped, and the stop looked like completeness. The
//      model chased 233(3), hit the dead end, and wrote "Section 233(3) enables
//      site plan approval … it does not override the 8-unit cap" — a reading
//      that appears in no fragment of the corpus.
//
// The fixture (scripts/seed_e2e_abs523_footnote_retention.py) carries both
// shapes. No other permission-matrix fixture in the suite has a multi-marker
// cell, and none has a dangling bracketed container, so none of them can see
// either defect. The clauses are parented to the heading and the stem is left
// unpathed, exactly as the corpus holds them — repairing either in the fixture
// would grade the unfixed code as fixed.
//
// Drives the real retrieval service through /v1/_test/zone-profile (the compact
// projection the model reads) and /v1/_test/lookup-citation.

import type { APIRequestContext } from "@playwright/test";
import { execSync } from "node:child_process";
import * as path from "node:path";

import { E2E_API_URL, expect, test } from "../fixtures/test-env";
import { resolveDatabaseUrl } from "../helpers/database-url";

const BYLAW_NAME = "Footnote Retention Test By-law";
const SECTION_PATH = "Part V > 233";

/** ⑮ — the carve-out that used to be the only survivor of the ER-3 cell. */
const GRAIN_ELEVATOR = 15;
/** ㉒ — the footnote that authorises more than 8 units in ER-3. */
const CONVERSION_ROUTE = 22;

type Condition = { footnote: number; text?: string };

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
      "seed_e2e_abs523_footnote_retention.py",
    )}"`,
    { env, stdio: "inherit" },
  );
}

// Binds the matrix axes so (use, zone) addresses a cell, and hands back the
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

async function permittedUse(
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
  expect(
    response.status(),
    `lookup-citation permitted_use failed: ${await response.text()}`,
  ).toBe(200);
  const body = await response.json();
  expect(body.permitted_use, `no permitted_use result for ${use} / ${zone}`).toBeTruthy();
  return body.permitted_use;
}

let documentId: number;

test.beforeAll(async ({ request }) => {
  runSeeds();
  documentId = await enrich(request);
});

test("the ER-3 cell reports both of its conditions, not just the first", async ({
  request,
}) => {
  const result = await permittedUse(request, "Multi-unit dwelling use", "ER-3");

  expect(result.indeterminate).toBe(false);
  expect(result.permission).toBe("conditional");

  const ordinals = (result.footnotes ?? []).map(
    (f: { ordinal: number }) => f.ordinal,
  );
  expect(
    ordinals,
    "the cell prints two markers and both bind — this is the ABS-523 defect",
  ).toEqual([GRAIN_ELEVATOR, CONVERSION_ROUTE]);

  // The legends, not just the ordinals: an ordinal with no text tells a reader
  // a condition exists and nothing about what it says.
  const texts: string[] = (result.footnotes ?? []).map(
    (f: { text?: string }) => f.text ?? "",
  );
  expect(texts[0]).toContain("Halifax Grain Elevator");
  // The two routes TC-023 never heard about.
  expect(texts[1]).toContain("Section 63");
  expect(texts[1]).toContain("233(3)");
});

test("a single-marker cell is unchanged", async ({ request }) => {
  // The fix must not manufacture conditions where the table states one.
  const result = await permittedUse(request, "Multi-unit dwelling use", "ER-2");
  const ordinals = (result.footnotes ?? []).map(
    (f: { ordinal: number }) => f.ordinal,
  );
  expect(ordinals).toEqual([GRAIN_ELEVATOR]);
});

test("the zone profile the agent opens a case with carries both", async ({
  request,
}) => {
  // get_zone_profile is what the server instructions tell the agent to call
  // first, so a condition dropped here is its first impression of the zone and
  // the last thing it will think to re-check.
  const response = await request.post(`${E2E_API_URL}/v1/_test/zone-profile`, {
    headers: { "Content-Type": "application/json" },
    data: { zone: "ER-3", document_id: documentId },
  });
  expect(response.status(), `zone-profile failed: ${await response.text()}`).toBe(200);
  const body = await response.json();
  expect(body.unknown_zone).toBe(false);

  const conditional = body.profile.uses.conditional ?? [];
  const multiUnit = conditional.find(
    (item: { use: string }) => item.use === "Multi-unit dwelling use",
  );
  expect(multiUnit, "the conditional use is missing from the profile").toBeTruthy();

  const conditions: Condition[] = multiUnit.conditions ?? [];
  expect(conditions.map((c) => c.footnote)).toEqual([
    GRAIN_ELEVATOR,
    CONVERSION_ROUTE,
  ]);
  expect(conditions[1].text).toContain("Section 63");
});

test("lookup_citation on s.233 returns the rear-addition limb, stem included", async ({
  request,
}) => {
  const response = await request.post(`${E2E_API_URL}/v1/_test/lookup-citation`, {
    headers: { "Content-Type": "application/json" },
    data: { citation_path: SECTION_PATH, document_id: documentId },
  });
  expect(response.status(), `lookup-citation failed: ${await response.text()}`).toBe(200);
  const body = await response.json();
  expect(body.match, `no match at ${SECTION_PATH}`).toBeTruthy();

  const clauses: Array<{ text: string; citation_path: string | null }> =
    body.match.operative_clauses ?? [];
  const texts = clauses.map((c) => c.text);

  // What the call used to return, and still must.
  expect(texts.some((t) => t.includes("building width of 20.0 metres"))).toBe(true);
  expect(texts.some((t) => t.includes("building depth of 30.0 metres"))).toBe(true);

  // What it dropped: the whole rear-addition limb, one level down under a
  // bracketed segment that names no fragment.
  const stemIndex = texts.findIndex((t) => t.startsWith("An addition to an existing"));
  expect(
    stemIndex,
    "the s.233(3) stem is still missing — the model has to invent what the " +
      "clauses below it are conditions on",
  ).toBeGreaterThanOrEqual(0);
  expect(texts.some((t) => t.includes("more than 8 dwelling units in an ER-3 zone"))).toBe(
    true,
  );

  // The stem has no citation path of its own — which is exactly why completion
  // is the only route to it and why lookup_citation could not reach it before.
  expect(clauses[stemIndex].citation_path).toBeNull();
  // Reading order: the stem lands before the clauses it introduces, not
  // appended after them.
  const er3Index = texts.findIndex((t) =>
    t.includes("more than 8 dwelling units in an ER-3 zone"),
  );
  expect(stemIndex).toBeLessThan(er3Index);

  // Nothing was silently cut.
  expect(body.match.operative_clauses_omitted).toBe(0);
});

test("the bracketed segment names no fragment of its own", async ({ request }) => {
  // The premise of the whole fix. If a later ingest gives s.233(3) a real path
  // this fails, and that is the right outcome: the content-addressed fallback
  // is then unnecessary rather than merely still green.
  const response = await request.post(`${E2E_API_URL}/v1/_test/lookup-citation`, {
    headers: { "Content-Type": "application/json" },
    data: {
      citation_path:
        "Part V > 233 > [An addition to an existing main building ... main building to contain]",
      document_id: documentId,
    },
  });
  expect(response.status()).toBe(200);
  const body = await response.json();
  expect(body.match, "something now stands at the bracketed segment").toBeNull();
});
