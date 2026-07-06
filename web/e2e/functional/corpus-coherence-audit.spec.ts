// Functional regression: ABS-356 — the corpus-coherence audit catches a
// linked overlay dataset falling out of retrieval scope loudly, instead of
// the silent degradation that shipped in the ABS-349/ABS-350 saga (a map
// layer's link breaks and get_address_profile just starts returning nulls,
// discovered downstream in an unrelated failure or a customer-visible
// hedged answer).
//
// Approach
// --------
// beforeAll seeds TWO throwaway bylaws via scripts/seed_e2e_corpus_coherence.py:
//   * "Corpus Coherence Test Bylaw" — zone + height_precinct, both linked
//     (the coherent control).
//   * "Corpus Coherence Broken-Link Test Bylaw" — the same two roles, but
//     height_precinct's linked_fragment_id is nulled out after ingest (the
//     exact "orphan" condition layer1.datasets.linker already names).
// Both fixtures are seeded once, up front, deterministically — this repo's
// Playwright config runs `fullyParallel` across four viewport projects
// sharing one Postgres DB, so every test below only *reads* via
// POST /v1/_test/corpus-coherence; nothing mutates state mid-test, which
// would otherwise race a concurrent project's assertions against the same
// fixture.

import { execSync } from "node:child_process";
import * as path from "node:path";

import { E2E_API_URL, expect, test } from "../fixtures/test-env";

const COHERENT_MUNICIPALITY = "E2E Coherence Municipality";
const COHERENT_BYLAW_NAME = "Corpus Coherence Test Bylaw";

const BROKEN_MUNICIPALITY = "E2E Coherence Broken-Link Municipality";
const BROKEN_BYLAW_NAME = "Corpus Coherence Broken-Link Test Bylaw";

const COHERENT_ZONE_DECLARATION = {
  dataset_name: "e2e_coherence_zoning_boundaries",
  municipality: COHERENT_MUNICIPALITY,
  bylaw_name: COHERENT_BYLAW_NAME,
  fragment_citation: "Zoning Schedule",
};

const COHERENT_HEIGHT_PRECINCT_DECLARATION = {
  dataset_name: "e2e_coherence_height_precincts",
  municipality: COHERENT_MUNICIPALITY,
  bylaw_name: COHERENT_BYLAW_NAME,
  fragment_citation: "Schedule 15",
};

const BROKEN_ZONE_DECLARATION = {
  dataset_name: "e2e_coherence_zoning_boundaries_broken",
  municipality: BROKEN_MUNICIPALITY,
  bylaw_name: BROKEN_BYLAW_NAME,
  fragment_citation: "Zoning Schedule",
};

const BROKEN_HEIGHT_PRECINCT_DECLARATION = {
  dataset_name: "e2e_coherence_height_precincts_broken",
  municipality: BROKEN_MUNICIPALITY,
  bylaw_name: BROKEN_BYLAW_NAME,
  fragment_citation: "Schedule 15",
};

type MissingOverlayRole = {
  role: string;
  dataset_name: string;
  municipality: string;
  bylaw_name: string;
  fragment_citation: string;
  reason: "unlinked" | "orphaned" | "evicted";
  detail: string;
};

type CorpusCoherenceReport = {
  coherent: boolean;
  checked_roles: number;
  bylaws_checked: number;
  missing: MissingOverlayRole[];
};

function runSeed(): void {
  const repoRoot = path.resolve(__dirname, "..", "..", "..");
  const seed = path.join(repoRoot, "scripts", "seed_e2e_corpus_coherence.py");
  const venvPython = path.join(repoRoot, ".venv", "bin", "python");
  // ABS-207: honor PG_PORT so this seed lands in the right Postgres when a
  // worktree overrides ports for parallel `make e2e`.
  const pgPort = process.env.PG_PORT || "5432";
  const databaseUrl =
    process.env.DATABASE_URL ||
    `postgresql+psycopg://layer1:layer1@localhost:${pgPort}/layer1_test`;

  execSync(`"${venvPython}" "${seed}"`, {
    env: {
      ...process.env,
      DATABASE_URL: databaseUrl,
      PYTHONPATH: `${path.join(repoRoot, "src")}:${path.join(repoRoot, "mcp")}:${process.env.PYTHONPATH || ""}`,
    },
    stdio: "inherit",
  });
}

async function postAudit(
  request: import("@playwright/test").APIRequestContext,
  overlayDeclarations: Array<Record<string, string>>,
): Promise<CorpusCoherenceReport> {
  const response = await request.post(`${E2E_API_URL}/v1/_test/corpus-coherence`, {
    headers: { "Content-Type": "application/json" },
    data: { overlay_declarations: overlayDeclarations },
  });
  expect(
    response.status(),
    `corpus-coherence endpoint failed: ${response.status()} ${await response.text()}`,
  ).toBe(200);
  return (await response.json()) as CorpusCoherenceReport;
}

test.beforeAll(() => {
  runSeed();
});

test("a coherent corpus reports every declared overlay role visible", async ({ request }) => {
  const report = await postAudit(request, [
    COHERENT_ZONE_DECLARATION,
    COHERENT_HEIGHT_PRECINCT_DECLARATION,
  ]);

  expect(report.coherent).toBe(true);
  expect(report.missing).toEqual([]);
  expect(report.checked_roles).toBe(2);
  expect(report.bylaws_checked).toBe(1);
});

test("a deliberately broken link fails the audit, naming the missing role", async ({
  request,
}) => {
  // height_precinct's linked_fragment_id was nulled by the seed script — the
  // dataset row and its features still exist, only the link is broken.
  const report = await postAudit(request, [
    BROKEN_ZONE_DECLARATION,
    BROKEN_HEIGHT_PRECINCT_DECLARATION,
  ]);

  expect(report.coherent).toBe(false);
  expect(report.missing).toHaveLength(1);
  const missing = report.missing[0];
  expect(missing.role).toBe("height_precinct");
  expect(missing.dataset_name).toBe("e2e_coherence_height_precincts_broken");
  expect(missing.reason).toBe("orphaned");
  expect(missing.bylaw_name).toBe(BROKEN_BYLAW_NAME);

  // The zone overlay on the SAME bylaw was untouched — the audit reports
  // the break with per-role precision, not a blanket "something is wrong"
  // for the whole bylaw.
  expect(report.missing.some((m) => m.role === "zone")).toBe(false);
});

test("an overlay role no dataset was ever ingested for is reported as unlinked", async ({
  request,
}) => {
  const neverIngested = {
    dataset_name: "e2e_coherence_shadow_impact_areas",
    municipality: COHERENT_MUNICIPALITY,
    bylaw_name: COHERENT_BYLAW_NAME,
    fragment_citation: "Schedule 51",
  };

  const report = await postAudit(request, [neverIngested]);

  expect(report.coherent).toBe(false);
  expect(report.missing).toHaveLength(1);
  expect(report.missing[0].role).toBe("shadow_impact");
  expect(report.missing[0].reason).toBe("unlinked");
});
