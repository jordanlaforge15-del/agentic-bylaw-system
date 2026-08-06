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

function runSeedScript(scriptName: string, args: string[] = []): void {
  const repoRoot = path.resolve(__dirname, "..", "..", "..");
  const seed = path.join(repoRoot, "scripts", scriptName);
  const venvPython = path.join(repoRoot, ".venv", "bin", "python");
  // ABS-207: honor PG_PORT so this seed lands in the right Postgres when a
  // worktree overrides ports for parallel `make e2e`.
  const pgPort = process.env.PG_PORT || "5433";
  const databaseUrl =
    process.env.DATABASE_URL ||
    `postgresql+psycopg://layer1:layer1@localhost:${pgPort}/layer1_test`;

  const quotedArgs = args.map((a) => `"${a}"`).join(" ");
  execSync(`"${venvPython}" "${seed}" ${quotedArgs}`, {
    env: {
      ...process.env,
      DATABASE_URL: databaseUrl,
      PYTHONPATH: `${path.join(repoRoot, "src")}:${path.join(repoRoot, "mcp")}:${process.env.PYTHONPATH || ""}`,
    },
    stdio: "inherit",
  });
}

function runSeed(): void {
  runSeedScript("seed_e2e_corpus_coherence.py");
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

// ---------------------------------------------------------------------------
// ABS-432 — e2e-contamination tripwire
// ---------------------------------------------------------------------------
//
// Defense-in-depth behind the dev/e2e Postgres split (ABS-428) and the dev
// purge (ABS-429): rows fingerprinted by the e2e suite (parser_version
// 'e2e-seed', file_hash 'e2e-%', external_dataset name 'e2e_%') must be
// reported loudly if they ever reach a non-test database again.
//
// Two surfaces:
// * GET /v1/monitoring/corpus-coherence — the real ops endpoint. THIS stack
//   is the e2e deployment, whose DB legitimately holds seeded marker rows
//   (this very spec's beforeAll creates 'e2e-seed' documents), so the
//   endpoint reports them informationally ('expected_test_fixtures') and
//   never as 'contaminated'.
// * POST /v1/_test/e2e-contamination — the uncached, armed sweep (exactly
//   what a dev/prod tripwire evaluates). Each worker inserts its OWN
//   uniquely-hashed synthetic marker and asserts on that row only — four
//   viewport projects run this spec in parallel against one DB, so global
//   counts are not stable.

type E2eContaminationMarker = {
  table: "document" | "external_dataset";
  row_id: number;
  marker_kinds: string[];
  detail: string;
};

type E2eContaminationReport = {
  contaminated: boolean;
  marker_counts: Record<string, number>;
  markers: E2eContaminationMarker[];
};

async function postArmedContaminationSweep(
  request: import("@playwright/test").APIRequestContext,
): Promise<E2eContaminationReport> {
  const response = await request.post(`${E2E_API_URL}/v1/_test/e2e-contamination`, {
    headers: { "Content-Type": "application/json" },
  });
  expect(
    response.status(),
    `e2e-contamination endpoint failed: ${response.status()} ${await response.text()}`,
  ).toBe(200);
  return (await response.json()) as E2eContaminationReport;
}

test("monitoring corpus-coherence carries the e2e_contamination check and never reports the e2e stack's own fixtures as contamination", async ({
  request,
}) => {
  const response = await request.get(`${E2E_API_URL}/v1/monitoring/corpus-coherence`);
  // The e2e DB does not ingest the real halifax corpus, so the coherence
  // half of this endpoint may legitimately be 'incoherent' (503) here —
  // this test is about the contamination check's shape and its
  // markers-expected handling, not the coherence verdict.
  expect([200, 503]).toContain(response.status());
  const body = (await response.json()) as {
    status: string;
    e2e_contamination: E2eContaminationReport & { status: string };
  };

  expect(body.e2e_contamination).toBeDefined();
  const contamination = body.e2e_contamination;
  expect(typeof contamination.contaminated).toBe("boolean");
  expect(Object.keys(contamination.marker_counts).sort()).toEqual([
    "document_file_hash",
    "document_parser_version",
    "external_dataset_name",
  ]);
  // The e2e entrypoint declares ADVISOR_E2E_MARKERS_EXPECTED, so seeded
  // fixtures are reported informationally — never as 'contaminated', and
  // never flipping the top-level status to 'contaminated'.
  expect(["ok", "expected_test_fixtures"]).toContain(contamination.status);
  expect(body.status).not.toBe("contaminated");
});

test("the armed sweep goes red on a synthetic marker row, names it, and clears after cleanup", async ({
  request,
}, testInfo) => {
  const fileHash = `e2e-tripwire-w${testInfo.workerIndex}-${Date.now().toString(36)}`;

  runSeedScript("seed_e2e_contamination_marker.py", ["--file-hash", fileHash]);
  try {
    const dirty = await postArmedContaminationSweep(request);

    // Red: the sweep a dev/prod deployment runs judges this DB contaminated
    // and names our synthetic row with both document marker kinds.
    expect(dirty.contaminated).toBe(true);
    const ours = dirty.markers.find((m) => m.detail.includes(fileHash));
    expect(ours, `synthetic marker ${fileHash} not named in sweep`).toBeDefined();
    expect(ours!.table).toBe("document");
    expect(ours!.marker_kinds).toEqual(
      expect.arrayContaining(["document_parser_version", "document_file_hash"]),
    );
  } finally {
    runSeedScript("seed_e2e_contamination_marker.py", ["--file-hash", fileHash, "--delete"]);
  }

  // Green (for this row): after cleanup the sweep no longer names it.
  const clean = await postArmedContaminationSweep(request);
  expect(clean.markers.some((m) => m.detail.includes(fileHash))).toBe(false);
});

// ---------------------------------------------------------------------------
// ABS-434 — enabled-name-collision audit
// ---------------------------------------------------------------------------
//
// The doc-15/38 double-enable: two ENABLED documents whose bylaw names
// differ only by casing ("By-law" vs "By-Law") fragment the enabled corpus
// because every exact-match pass (backfill, --replace, relink) sees them as
// unrelated bylaws. The audit groups enabled documents by their normalized
// (municipality, bylaw_name) and reports any group with >1 member.
//
// Same surface split as ABS-432 above:
// * GET /v1/monitoring/corpus-coherence carries the check (cached 30s) —
//   asserted on shape only, since parallel workers may transiently seed
//   collisions of their own.
// * POST /v1/_test/enabled-name-collisions is the raw, uncached audit the
//   red/green assertions drive. Each caller seeds its OWN uniquely-slugged
//   pair under its own municipality and asserts on those ids only.

type EnabledNameCollision = {
  normalized_municipality: string;
  normalized_bylaw_name: string;
  document_ids: number[];
  documents: Array<{ id: number; municipality: string; bylaw_name: string }>;
  detail: string;
};

type EnabledNameCollisionReport = {
  collision_free: boolean;
  enabled_documents: number;
  identities_checked: number;
  collisions: EnabledNameCollision[];
};

function runSeedScriptCapture(scriptName: string, args: string[] = []): string {
  const repoRoot = path.resolve(__dirname, "..", "..", "..");
  const seed = path.join(repoRoot, "scripts", scriptName);
  const venvPython = path.join(repoRoot, ".venv", "bin", "python");
  const pgPort = process.env.PG_PORT || "5433";
  const databaseUrl =
    process.env.DATABASE_URL ||
    `postgresql+psycopg://layer1:layer1@localhost:${pgPort}/layer1_test`;

  const quotedArgs = args.map((a) => `"${a}"`).join(" ");
  return execSync(`"${venvPython}" "${seed}" ${quotedArgs}`, {
    env: {
      ...process.env,
      DATABASE_URL: databaseUrl,
      PYTHONPATH: `${path.join(repoRoot, "src")}:${path.join(repoRoot, "mcp")}:${process.env.PYTHONPATH || ""}`,
    },
    encoding: "utf-8",
  });
}

async function postNameCollisionAudit(
  request: import("@playwright/test").APIRequestContext,
): Promise<EnabledNameCollisionReport> {
  const response = await request.post(`${E2E_API_URL}/v1/_test/enabled-name-collisions`, {
    headers: { "Content-Type": "application/json" },
  });
  expect(
    response.status(),
    `enabled-name-collisions endpoint failed: ${response.status()} ${await response.text()}`,
  ).toBe(200);
  return (await response.json()) as EnabledNameCollisionReport;
}

test("monitoring corpus-coherence carries the enabled_name_collisions check", async ({
  request,
}) => {
  const response = await request.get(`${E2E_API_URL}/v1/monitoring/corpus-coherence`);
  expect([200, 503]).toContain(response.status());
  const body = (await response.json()) as {
    enabled_name_collisions: EnabledNameCollisionReport & { status: string };
  };

  const collisions = body.enabled_name_collisions;
  expect(collisions).toBeDefined();
  expect(typeof collisions.collision_free).toBe("boolean");
  expect(typeof collisions.enabled_documents).toBe("number");
  expect(typeof collisions.identities_checked).toBe("number");
  expect(["ok", "collision"]).toContain(collisions.status);
  expect(collisions.status === "ok").toBe(collisions.collision_free);
});

test("two enabled case-variant documents fail the audit naming both ids; disabling one heals it", async ({
  request,
}, testInfo) => {
  const slug = `w${testInfo.workerIndex}-${Date.now().toString(36)}`;

  const seedOut = runSeedScriptCapture("seed_e2e_name_collision.py", ["--slug", slug]);
  const seededLine = seedOut.split("\n").find((l) => l.startsWith("SEEDED "));
  expect(seededLine, `seed script printed no SEEDED line: ${seedOut}`).toBeDefined();
  const { document_ids: seededIds } = JSON.parse(seededLine!.slice("SEEDED ".length)) as {
    document_ids: number[];
  };
  expect(seededIds).toHaveLength(2);

  try {
    // Red: the audit reports OUR pair as one collision naming both ids and
    // both stored spellings.
    const dirty = await postNameCollisionAudit(request);
    expect(dirty.collision_free).toBe(false);
    const ours = dirty.collisions.find(
      (c) => c.document_ids.includes(seededIds[0]) && c.document_ids.includes(seededIds[1]),
    );
    expect(ours, `seeded pair ${seededIds} not reported in ${JSON.stringify(dirty.collisions)}`).toBeDefined();
    const spellings = ours!.documents.map((d) => d.bylaw_name).sort();
    expect(spellings).toEqual([
      "Name Collision Tripwire By-Law (ABS-434 E2E)",
      "Name Collision Tripwire By-law (ABS-434 E2E)",
    ]);
    expect(ours!.detail).toContain(String(seededIds[0]));
    expect(ours!.detail).toContain(String(seededIds[1]));

    // Green: disable one side (the operator's heal) — our group disappears.
    runSeedScriptCapture("seed_e2e_name_collision.py", ["--slug", slug, "--disable-second"]);
    const healed = await postNameCollisionAudit(request);
    expect(
      healed.collisions.some((c) => c.document_ids.some((id) => seededIds.includes(id))),
    ).toBe(false);
  } finally {
    runSeedScriptCapture("seed_e2e_name_collision.py", ["--slug", slug, "--delete"]);
  }
});
