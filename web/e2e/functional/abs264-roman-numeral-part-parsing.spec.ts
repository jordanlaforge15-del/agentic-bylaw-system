// ABS-264: Layer 1 hierarchy parser must capture multi-character Roman-numeral
// Part labels (Part II, Part III, Part IV, …) rather than truncating them to
// the first character.
//
// Root cause: PART_RE in citations.py and the Halifax manifest's part pattern
// both used [A-Z] — a single-character class. With re.IGNORECASE, [A-Z] matches
// exactly *one* letter, so "Part IV" was tokenised as label="Part I" with "V"
// silently absorbed into the heading title. Parts II, III, IV, VI–IX, XI–XX
// were all misidentified or missed, corrupting citation_path for thousands of
// Halifax Regional Centre fragments.
//
// Fix: replace [A-Z] with [IVXLCDM]+|[A-Z] in both the manifest pattern and
// the hardcoded PART_RE fallback. The Roman-numeral alternative is listed first
// so "IV" is captured greedily rather than matching only "I".
//
// This spec drives the /v1/_test/manifest-ingest endpoint against a synthetic
// four-part bylaw using Roman-numeral Part headings (Part I … Part IV) and the
// Halifax Regional Centre manifest (the only committed manifest whose part
// pattern includes the IVXLCDM alternative). A pre-fix build would return four
// citation paths all starting with "Part I >" — a post-fix build must return
// paths starting with "Part I >", "Part II >", "Part III >", and "Part IV >"
// as distinct top-level parts.

import * as fs from "node:fs";
import * as os from "node:os";
import * as path from "node:path";

import { E2E_API_URL, expect, test } from "../fixtures/test-env";

const REPO_ROOT = path.resolve(__dirname, "..", "..", "..");
const HALIFAX_MANIFEST = path.join(
  REPO_ROOT,
  "abs-learning",
  "output",
  "halifax-regional-centre",
  "manifest.json",
);

// Synthetic bylaw with four Roman-numeral Parts. Each Part has one Section so
// the ingest produces at least one citation_path per Part — giving us something
// concrete to assert on without needing a real PDF.
const ROMAN_NUMERAL_BYLAW_BODY = [
  "Halifax Regional Centre Land Use Bylaw",
  "Part I General Provisions",
  "1 Purpose",
  "This bylaw governs land use in the Halifax Regional Centre.",
  "Part II Residential Zones",
  "2 R-1 Zone",
  "Single-detached dwellings are permitted in the R-1 zone.",
  "Part III Commercial Zones",
  "3 C-1 Zone",
  "Retail commercial uses are permitted in the C-1 zone.",
  "Part IV Industrial Zones",
  "4 I-1 Zone",
  "Light industrial uses are permitted in the I-1 zone.",
  "",
].join("\n");

type IngestResponse = {
  ok: boolean;
  document_id?: number;
  municipality?: string;
  fragment_count?: number;
  citation_paths?: string[];
  errors?: string[];
};

test.describe("ABS-264: Roman-numeral Part parsing via manifest-driven ingest", () => {
  test("four Roman-numeral Parts produce four distinct top-level citation path prefixes", async ({
    request,
  }) => {
    expect(fs.existsSync(HALIFAX_MANIFEST)).toBe(true);

    const tmpDir = fs.mkdtempSync(
      path.join(os.tmpdir(), "abs-264-roman-parts-"),
    );
    const bylawPath = path.join(tmpDir, "roman-parts.txt");
    fs.writeFileSync(bylawPath, ROMAN_NUMERAL_BYLAW_BODY, "utf-8");

    try {
      const response = await request.post(
        `${E2E_API_URL}/v1/_test/manifest-ingest`,
        {
          headers: { "Content-Type": "application/json" },
          data: {
            manifest_path: HALIFAX_MANIFEST,
            bylaw_path: bylawPath,
            bylaw_name: "ABS-264 Roman-numeral Parts E2E",
          },
        },
      );
      expect(
        response.status(),
        `ingest failed; body: ${await response.text()}`,
      ).toBe(200);

      const body = (await response.json()) as IngestResponse;
      expect(body.ok).toBe(true);
      expect(body.fragment_count ?? 0).toBeGreaterThan(0);

      const paths = body.citation_paths ?? [];
      expect(paths.length).toBeGreaterThan(0);

      // Extract distinct top-level Part labels from citation_paths.
      // Each path is formatted like "Part IV > Section 4 > …" — we grab
      // the first segment to see which Part was captured.
      const topLevelParts = [
        ...new Set(paths.map((p) => p.split(">")[0].trim())),
      ].sort();

      // The four parts must appear as distinct labels. A pre-fix build would
      // collapse Part II, III, IV all to "Part I" because [A-Z] matches only
      // the leading "I" — so topLevelParts would be ["Part I"] (length 1).
      expect(
        topLevelParts,
        `Expected 4 distinct Part labels but got: ${JSON.stringify(topLevelParts)}`,
      ).toHaveLength(4);

      expect(topLevelParts).toContain("Part I");
      expect(topLevelParts).toContain("Part II");
      expect(topLevelParts).toContain("Part III");
      expect(topLevelParts).toContain("Part IV");
    } finally {
      fs.rmSync(tmpDir, { recursive: true, force: true });
    }
  });
});
