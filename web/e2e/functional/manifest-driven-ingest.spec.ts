// ABS-74: Manifest-driven Layer 1 ingest, end-to-end through the real stack.
//
// What this proves:
//
// - A CityIntakeManifest sitting on disk under
//   abs-learning/output/{jurisdiction_code}/manifest.json is sufficient input
//   to drive Layer 1 ingest with no hand-edited ParsingProfile (acceptance
//   #1 on the issue).
// - The manifest's pipeline_ready flag is a meaningful gate — flipping it
//   to false causes the ingest to refuse with a 4xx (acceptance #3).
// - Semantic enrichment honors the manifest's zone codes: the ingested
//   document's zone entities are drawn from the manifest taxonomy, not
//   the hardcoded KNOWN_ZONE_CODES set in extractors.py.
//
// The spec drives the test-only POST /v1/_test/manifest-ingest endpoint on
// the e2e FastAPI server. That endpoint is the same shape as the existing
// /v1/_test/evaluate-bylaws endpoint used by the evaluator spec — it
// exercises the real ingestion code path through HTTP + Postgres so a
// misconfigured proxy, missing migration, or regressed manifest_adapter
// would trip e2e.

import * as fs from "node:fs";
import * as os from "node:os";
import * as path from "node:path";

import { E2E_API_URL, expect, test } from "../fixtures/test-env";

const REPO_ROOT = path.resolve(__dirname, "..", "..", "..");
const SAMPLETON_MANIFEST = path.join(
  REPO_ROOT,
  "abs-learning",
  "output",
  "sampleton",
  "manifest.json",
);
const SYNTHETIC_BYLAW = path.join(
  REPO_ROOT,
  "tests",
  "fixtures",
  "synthetic_bylaw.txt",
);

type IngestResponse = {
  ok: boolean;
  document_id?: number;
  municipality?: string;
  bylaw_name?: string;
  parser_version?: string | null;
  jurisdiction_code?: string | null;
  fragment_count?: number;
  citation_paths?: string[];
  zone_entities?: string[];
  manifest_zone_codes?: string[];
  enrichment?: { entities: number };
  errors?: string[];
};

test("manifest-driven ingest succeeds for a non-Halifax city with no hand-edited profile", async ({
  request,
}) => {
  // Sanity: the inputs the endpoint will read must exist on disk.
  expect(fs.existsSync(SAMPLETON_MANIFEST)).toBe(true);
  expect(fs.existsSync(SYNTHETIC_BYLAW)).toBe(true);

  const response = await request.post(`${E2E_API_URL}/v1/_test/manifest-ingest`, {
    headers: { "Content-Type": "application/json" },
    data: {
      manifest_path: SAMPLETON_MANIFEST,
      bylaw_path: SYNTHETIC_BYLAW,
      bylaw_name: "ABS-74 Sampleton Manifest E2E",
    },
  });
  expect(response.status()).toBe(200);

  const body = (await response.json()) as IngestResponse;
  expect(body.ok).toBe(true);
  expect(body.document_id).toBeGreaterThan(0);
  expect(body.municipality).toBe("Town of Sampleton");
  expect(body.jurisdiction_code).toBe("sampleton");
  // The profile name the adapter synthesises is encoded in parser_version.
  expect(body.parser_version ?? "").toMatch(/manifest:sampleton/);

  // Acceptance: ingest produced citations.
  expect(body.fragment_count ?? 0).toBeGreaterThan(0);
  expect(body.citation_paths?.length ?? 0).toBeGreaterThan(0);
  expect(body.citation_paths?.[0]).toContain("Part ");

  // Acceptance: the manifest's zone codes overlay the hardcoded set. The
  // synthetic bylaw text mentions "R1" — a Sampleton zone code from the
  // manifest, not present in the Halifax KNOWN_ZONE_CODES. With the
  // manifest overlay active, the zone enrichment should surface "R1".
  expect(body.manifest_zone_codes).toEqual(["C1", "I1", "R1", "R2"]);
  expect(body.zone_entities).toEqual(expect.arrayContaining(["R1"]));
});

test("manifest-driven ingest refuses to run when pipeline_ready=false", async ({
  request,
}) => {
  // Write a not-ready manifest to a tmp file and point the endpoint at it.
  // We can't mutate the committed Sampleton manifest — production-style
  // ingests need it to stay pipeline_ready=true.
  const tmpDir = fs.mkdtempSync(path.join(os.tmpdir(), "abs-74-not-ready-"));
  const notReadyPath = path.join(tmpDir, "manifest.json");
  const raw = fs.readFileSync(SAMPLETON_MANIFEST, "utf-8");
  const draft = JSON.parse(raw);
  draft.pipeline_ready = false;
  draft.status = "draft";
  fs.writeFileSync(notReadyPath, JSON.stringify(draft), "utf-8");

  try {
    const response = await request.post(`${E2E_API_URL}/v1/_test/manifest-ingest`, {
      headers: { "Content-Type": "application/json" },
      data: {
        manifest_path: notReadyPath,
        bylaw_path: SYNTHETIC_BYLAW,
        bylaw_name: "ABS-74 Sampleton Not-Ready E2E",
      },
    });
    // The endpoint translates ManifestNotReadyError into HTTP 409 (Conflict)
    // — pipeline_ready=true is a precondition the caller must satisfy.
    expect(response.status()).toBe(409);
    const detail = ((await response.json()) as { detail: string }).detail;
    expect(detail).toContain("pipeline_ready=False");
  } finally {
    fs.rmSync(tmpDir, { recursive: true, force: true });
  }
});
