// ABS-488: a clause's citation path carries the container that scopes it.
//
// 720 labelled, citable provisions of document 4 (16.6% of the by-law) had no
// citation_path at all. The cause was a naming bug, not a parse failure: a
// clause's path carried the *sticky heading* last seen rather than the
// subsection, stem or definition it actually sits under, so section 9's two
// clause groups both computed
// "Part I > 9 > [Development Permit Exemptions] > (a)". The collision rule then
// blanked both, which is how a naming bug became an unreachable clause.
// Part headings had the mirror problem: all eight of Part I's chapters parse as
// the bare label "Part I" and collided the same way.
//
// This spec drives /v1/_test/lookup-citation — the same RetrievalService path
// the LLM tool loop calls — against a probe corpus that
// scripts/seed_e2e_abs488_citation_repath.py builds by running the real
// hierarchy parser over ten blocks:
//
//   Part I: Administration                      -> "Part I"
//   Part I, Chapter 1: General Administration   -> "Part I, Chapter 1"
//   Part I, Chapter 2: Development Permit       -> "Part I, Chapter 2"
//   Development Permit Exemptions               (heading, no path)
//   9 No development permit is required ...     -> "Part I > 9"
//   (a) accessory structures ...                -> "Part I > 9 > (a)"
//   (b) kiosks ...                              -> "Part I > 9 > (b)"
//   On a registered heritage property ...       (stem, no path)
//   (a) uncovered structures ...                -> "... > [stem] > (a)"
//   (b) fences.                                 -> "... > [stem] > (b)"
//
// Every lookup is scoped by document_id to the probe document, so no other
// seeded corpus can satisfy a path or contribute a suggestion.

import { execSync } from "node:child_process";
import * as path from "node:path";

import { E2E_API_URL, expect, test } from "../fixtures/test-env";

const BYLAW_NAME = "Citation Repath Probe Bylaw (ABS-488 E2E)";
const STEM = "On a registered heritage property, a development permit shall be required for";

const SECTION_PATH = "Part I > 9";
const FIRST_GROUP = ["Part I > 9 > (a)", "Part I > 9 > (b)"];
const SECOND_GROUP = [
  `Part I > 9 > [${STEM}] > (a)`,
  `Part I > 9 > [${STEM}] > (b)`,
];
const CHAPTER_PATHS = ["Part I", "Part I, Chapter 1", "Part I, Chapter 2"];

let documentId: number | null = null;

test.beforeAll(() => {
  const repoRoot = path.resolve(__dirname, "..", "..", "..");
  const venvPython = path.join(repoRoot, ".venv", "bin", "python");
  const pgPort = process.env.PG_PORT || "5433";
  const databaseUrl =
    process.env.DATABASE_URL ||
    `postgresql+psycopg://layer1:layer1@localhost:${pgPort}/layer1_test`;
  const output = execSync(
    `"${venvPython}" "${path.join(repoRoot, "scripts", "seed_e2e_abs488_citation_repath.py")}"`,
    {
      env: {
        ...process.env,
        DATABASE_URL: databaseUrl,
        PYTHONPATH: `${path.join(repoRoot, "src")}:${path.join(repoRoot, "mcp")}:${process.env.PYTHONPATH || ""}`,
      },
      encoding: "utf-8",
    },
  );
  const matched = output.match(/document=(\d+)/);
  documentId = matched ? parseInt(matched[1], 10) : null;
  expect(documentId, `seed did not report a document id; output: ${output}`).not.toBeNull();
});

type LookupBody = {
  match: { citation_path?: string; citation_label?: string; text?: string; parse_status?: string } | null;
  suggestions: string[];
};

async function lookup(
  request: import("@playwright/test").APIRequestContext,
  citationPath: string,
): Promise<LookupBody> {
  const res = await request.post(`${E2E_API_URL}/v1/_test/lookup-citation`, {
    headers: { "Content-Type": "application/json" },
    data: { citation_path: citationPath, document_id: documentId },
  });
  expect(res.status(), `lookup-citation failed for ${citationPath}`).toBe(200);
  return (await res.json()) as LookupBody;
}

test.describe("ABS-488: the missing citation-path discriminator", () => {
  // DoD: no labelled fragment is left without a path. Both clause groups have
  // to be reachable, and reachable at *different* addresses.
  test("both clause groups under one section resolve to distinct clauses", async ({
    request,
  }) => {
    const resolved = await Promise.all(
      [...FIRST_GROUP, ...SECOND_GROUP].map((p) => lookup(request, p)),
    );

    for (const [index, body] of resolved.entries()) {
      const requested = [...FIRST_GROUP, ...SECOND_GROUP][index];
      expect(body.match, `${requested} did not resolve`).not.toBeNull();
      expect(body.match?.citation_path).toBe(requested);
      expect(body.match?.parse_status).toBe("parsed");
    }

    // The whole point: the two groups are different provisions.
    expect(resolved[0].match?.text).toContain("accessory structures");
    expect(resolved[2].match?.text).toContain("uncovered structures");
    expect(new Set(resolved.map((b) => b.match?.text)).size).toBe(4);
  });

  // The old shape must be gone, not merely shadowed — a corpus that answered
  // to both addresses would still be ambiguous.
  test("the heading-decorated path the collision came from no longer resolves", async ({
    request,
  }) => {
    const body = await lookup(request, "Part I > 9 > [Development Permit Exemptions] > (a)");
    expect(body.match).toBeNull();
    // ABS-261's contract: a miss is suggestions, never an exception.
    expect(Array.isArray(body.suggestions)).toBe(true);
  });

  test("each Part chapter heading has its own address", async ({ request }) => {
    const resolved = await Promise.all(CHAPTER_PATHS.map((p) => lookup(request, p)));

    for (const [index, body] of resolved.entries()) {
      expect(body.match, `${CHAPTER_PATHS[index]} did not resolve`).not.toBeNull();
      expect(body.match?.citation_path).toBe(CHAPTER_PATHS[index]);
    }
    expect(resolved[1].match?.text).toContain("Chapter 1");
    expect(resolved[2].match?.text).toContain("Chapter 2");
  });

  // The chapter discriminates the chapter heading only. Pushing it onto
  // descendants would move every stored section path in the corpus, which is
  // exactly what this change is designed not to do.
  test("a section under a chapter still cites the chapter-free Part", async ({ request }) => {
    const body = await lookup(request, SECTION_PATH);

    expect(body.match, `${SECTION_PATH} did not resolve`).not.toBeNull();
    expect(body.match?.citation_label).toBe("9");
    expect(body.match?.text).toContain("No development permit is required");
  });
});

test.afterAll(async ({ request }) => {
  // Remove the probe document so its paths cannot show up as suggestions in
  // the concurrently-running abs261 / abs270 lookup specs.
  await request.post(`${E2E_API_URL}/v1/_test/delete-documents`, {
    headers: { "Content-Type": "application/json" },
    data: { bylaw_name: BYLAW_NAME },
  });
});
