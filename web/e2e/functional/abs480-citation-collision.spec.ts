// ABS-480: a citation-path collision is a naming failure, not a parse failure.
//
// `_clear_duplicate_citation_paths` used to react to two provisions deriving
// the same citation_path by blanking the path (correct — the address really is
// ambiguous) AND flipping parse_status PARSED -> UNCERTAIN while capping
// confidence at 0.6 (wrong — the text parsed fine, a sibling just happened to
// compute the same address).
//
// That demotion is not cosmetic: `_score_fragment` awards +1.0 for a parsed
// fragment and -2.0 otherwise, so every collided provision carried a 3-point
// ranking penalty and was labelled "uncertain" in every response the advisor
// sees.
//
// This spec drives the real scorer at the FastAPI ↔ Postgres boundary via
// POST /v1/_test/search-evidence-raw against a probe corpus that
// scripts/seed_e2e_abs480_citation_collision.py builds by running the real
// hierarchy parser over five blocks:
//
//   26           section "26 Bicycle Parking"
//   (g) x2       both derive "26 > (g)" — the collision
//   (h)          derives "26 > (h)" — the control: same block type, same
//                clause pattern, same confidence, no collision
//   - bullet     an unlabelled list item the parser marks uncertain on its own
//                merits, which must stay uncertain. It carries a word more
//                than the clauses because a clause's citation_label is
//                indexed on top of the text it came from — see the seed.
//
// Every provision contains "bicycle" exactly once AND indexes to exactly nine
// tsvector tokens, so the query scores identically against all of them on
// content and the only thing separating their scores is the parse_status
// bonus. The token-count half of that is an ABS-494 requirement: FTS now
// blends into the text channel and ts_rank_cd normalises by length, so an
// unequal-length corpus would rank these on brevity and this spec would read
// the result as a collision penalty. The seed owns that invariant and
// documents how to re-check it after a reword.
//
// Note the ladder's 3.0-point parse_status spread arrives here halved: the
// text channel is a 50/50 blend of ladder and FTS, and FTS is by construction
// flat across this corpus. The assertions below are equality and strict
// inequality, so they hold for any blend weight — deliberately, since that
// weight is the knob ABS-494 swept.
//
// Every search is scoped by bylaw_name to the probe document, so no other
// seeded corpus can contribute matches.

import { execSync } from "node:child_process";
import * as path from "node:path";

import { test, expect, E2E_API_URL, DEMO_USER_ID } from "../fixtures/test-env";
import { resolveDatabaseUrl } from "../helpers/database-url";

const BYLAW_NAME = "Citation Collision Probe Bylaw (ABS-480 E2E)";
const COLLIDED_PATH = "26 > (g)";
const CONTROL_PATH = "26 > (h)";

test.beforeAll(() => {
  const repoRoot = path.resolve(__dirname, "..", "..", "..");
  const venvPython = path.join(repoRoot, ".venv", "bin", "python");
  const databaseUrl = resolveDatabaseUrl();
  const env = {
    ...process.env,
    DATABASE_URL: databaseUrl,
    PYTHONPATH: `${path.join(repoRoot, "src")}:${path.join(repoRoot, "mcp")}:${process.env.PYTHONPATH || ""}`,
  };
  execSync(
    `"${venvPython}" "${path.join(repoRoot, "scripts", "seed_e2e_abs480_citation_collision.py")}"`,
    { env, encoding: "utf-8" },
  );
});

type Match = {
  citation_label?: string | null;
  citation_path?: string | null;
  parse_status?: string;
  confidence?: number | null;
  text?: string;
  score?: number;
  metadata_json?: Record<string, unknown>;
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

function collidedClauses(matches: Match[]): Match[] {
  return matches.filter(
    (m) => m.metadata_json?.duplicate_citation_path === COLLIDED_PATH,
  );
}

// DoD-1: the collision is still recorded, and the path is still blanked —
// repathing is DM-11, not this issue.
test("both colliding clauses lose their path and record the collision", async ({
  request,
}) => {
  const collided = collidedClauses(await searchProbeCorpus(request, "bicycle"));

  expect(collided).toHaveLength(2);
  for (const match of collided) {
    expect(match.citation_path ?? null).toBeNull();
    expect(match.citation_label).toBe("(g)");
  }
});

// DoD-1 (the fix): the parse verdict survives the collision.
test("collided clauses stay parsed at their original confidence", async ({ request }) => {
  const matches = await searchProbeCorpus(request, "bicycle");
  const collided = collidedClauses(matches);
  const control = matches.find((m) => m.citation_path === CONTROL_PATH);

  expect(control, `control clause ${CONTROL_PATH} missing from results`).toBeTruthy();
  expect(control!.parse_status).toBe("parsed");

  expect(collided).toHaveLength(2);
  for (const match of collided) {
    // Before the fix: "uncertain" at the 0.6 cap.
    expect(match.parse_status).toBe("parsed");
    expect(match.confidence ?? 0).toBeCloseTo(control!.confidence ?? 0, 5);
    expect(match.confidence ?? 0).toBeGreaterThan(0.6);
  }
});

// DoD-1 (the consequence): the demotion cost 3 points in `_score_fragment`.
// The control clause is identical in every scoring input except that no
// sibling stole its address, so an equal score is the whole claim.
test("a collided clause scores exactly as well as its uncollided sibling", async ({
  request,
}) => {
  const matches = await searchProbeCorpus(request, "bicycle");
  const collided = collidedClauses(matches);
  const control = matches.find((m) => m.citation_path === CONTROL_PATH);

  expect(control?.score ?? 0).toBeGreaterThan(1.0);
  expect(collided).toHaveLength(2);
  for (const match of collided) {
    // Before the fix this was control.score - 3.0.
    expect(match.score ?? 0).toBeCloseTo(control!.score ?? 0, 5);
  }
});

// The fix restores what a collision demoted — it does not promote everything.
// The bullet list item is uncertain on its own merits and stays that way, so
// it keeps scoring below the clauses.
test("a natively uncertain fragment is not promoted by the fix", async ({ request }) => {
  const matches = await searchProbeCorpus(request, "bicycle");
  const bullet = matches.find((m) => (m.text ?? "").includes("Bicycle racks"));
  const collided = collidedClauses(matches);

  expect(bullet, "the bullet list item is missing from the results").toBeTruthy();
  expect(bullet!.parse_status).toBe("uncertain");
  expect(bullet!.metadata_json?.duplicate_citation_path).toBeUndefined();

  expect(collided).toHaveLength(2);
  for (const match of collided) {
    expect(match.score ?? 0).toBeGreaterThan(bullet!.score ?? 0);
  }
});
