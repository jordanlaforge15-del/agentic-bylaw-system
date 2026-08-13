// ABS-478: zone tokenization — no substring matching in _score_fragment.
//
// The retrieval scorer used to split "HR-2" into "hr" + "2" and test each
// token with `token in haystack`. "hr" is a substring of "through" and "2"
// of "2nd", so a zone-scoped query collected guaranteed noise on nearly
// every fragment it saw.
//
// This spec drives the real scorer at the FastAPI ↔ Postgres boundary via
// POST /v1/_test/search-evidence-raw (the full RetrievalResponse, scores
// included) against a two-fragment probe corpus seeded by
// scripts/seed_e2e_abs478_tokenization.py:
//
//   Part I > 1  "…project through the required yard above the 2nd storey."
//               — no zone code, no setback. Scored 9.0 before the fix (two
//                 substring artifacts + the parsed bonus), so it surfaced
//                 as a match. Must now score below the text-channel
//                 threshold and disappear.
//   Part I > 2  "In the HR-2 zone the minimum front and rear yard setbacks
//                are 3.0 metres." — the genuine hit, which must survive.
//
// Every search is scoped by bylaw_name to the probe document, so no other
// seeded corpus can contribute matches.

import { execSync } from "node:child_process";
import * as path from "node:path";

import { test, expect, E2E_API_URL, DEMO_USER_ID } from "../fixtures/test-env";

const BYLAW_NAME = "Zone Tokenization Probe Bylaw (ABS-478 E2E)";
const LOOKALIKE_PATH = "Part I > 1";
const GENUINE_PATH = "Part I > 2";

test.beforeAll(() => {
  const repoRoot = path.resolve(__dirname, "..", "..", "..");
  const venvPython = path.join(repoRoot, ".venv", "bin", "python");
  const pgPort = process.env.PG_PORT || "5433";
  const databaseUrl =
    process.env.DATABASE_URL ||
    `postgresql+psycopg://layer1:layer1@localhost:${pgPort}/layer1_test`;
  const env = {
    ...process.env,
    DATABASE_URL: databaseUrl,
    PYTHONPATH: `${path.join(repoRoot, "src")}:${path.join(repoRoot, "mcp")}:${process.env.PYTHONPATH || ""}`,
  };
  execSync(
    `"${venvPython}" "${path.join(repoRoot, "scripts", "seed_e2e_abs478_tokenization.py")}"`,
    { env, encoding: "utf-8" },
  );
});

type Match = {
  citation_path?: string;
  text?: string;
  score?: number;
};

async function searchProbeCorpus(
  request: import("@playwright/test").APIRequestContext,
  query: string,
): Promise<Match[]> {
  const resp = await request.post(`${E2E_API_URL}/v1/_test/search-evidence-raw`, {
    headers: {
      "Content-Type": "application/json",
      "X-Test-User-Id": DEMO_USER_ID,
    },
    data: { query, bylaw_name: BYLAW_NAME, limit: 10 },
  });
  expect(resp.ok(), `search-evidence-raw failed: ${resp.status()}`).toBeTruthy();
  const body = (await resp.json()) as { matches: Match[] };
  return body.matches ?? [];
}

// DoD-1: a zone-scoped query scores zero text-token points against a
// fragment that only *looks* like it contains the zone code's pieces.
test('"HR-2 setback" does not match a fragment containing "through" and "2nd"', async ({
  request,
}) => {
  const matches = await searchProbeCorpus(request, "HR-2 setback");
  const paths = matches.map((m) => m.citation_path);

  expect(paths).not.toContain(LOOKALIKE_PATH);
  // Belt and braces: nothing returned mentions the lookalike prose.
  for (const match of matches) {
    expect(match.text ?? "").not.toContain("2nd storey");
  }
});

// DoD-2: the fragment that actually contains HR-2 still gets the token hit.
test('"HR-2 setback" still matches the fragment that names HR-2', async ({ request }) => {
  const matches = await searchProbeCorpus(request, "HR-2 setback");

  expect(matches.map((m) => m.citation_path)).toContain(GENUINE_PATH);
  // It is the only thing the probe corpus should return for this query.
  expect(matches).toHaveLength(1);
  expect(matches[0].text).toContain("HR-2");
  expect(matches[0].score ?? 0).toBeGreaterThan(1.0);
});

// The zone code is addressable on its own, not just as part of a phrase.
test('a bare "HR-2" query resolves to the HR-2 fragment alone', async ({ request }) => {
  const matches = await searchProbeCorpus(request, "HR-2");

  expect(matches).toHaveLength(1);
  expect(matches[0].citation_path).toBe(GENUINE_PATH);
});

// The one genuinely useful thing substring matching did — matching an
// inflected form — is deliberately preserved: the query says "setback",
// the seeded fragment says "setbacks".
test('"setback" still matches text that writes "setbacks"', async ({ request }) => {
  const matches = await searchProbeCorpus(request, "yard setback");

  expect(matches.map((m) => m.citation_path)).toContain(GENUINE_PATH);
});
