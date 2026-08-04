// ABS-261: `lookup_citation` (and its HTTP surface at `/v1/citation`,
// proxied by the Next.js app at `/api/citation`) must NOT raise on a
// missed citation_path. Before this fix the service raised ValueError,
// the advisor's tool-use loop interpreted the resulting tool error as
// "retry with a different format," and burned through max_iterations
// re-issuing variants — ~2× the necessary API spend on ~38% of turns
// in the ABS-260 production-readiness sweep.
//
// The fix returns a CitationLookupResponse envelope with `match=null`
// and `suggestions: list[str]` carrying the nearest stored paths. The
// public /v1/citation HTTP endpoint preserves 404 on miss (the UI
// throws on non-200) but now embeds the suggestions in `detail`.
//
// This spec drives the proxied `/api/citation` route end-to-end against
// the e2e-seeded Evaluator E2E Bylaw fixture (see
// `scripts/seed_e2e_evaluator_bylaws.py`). The bylaw seeds three
// citation_paths: "4.2.1", "4.3.1", "4.4.1". We exercise three
// surfaces:
//
//   - hit:         `4.2.1`           → 200, body has citation_path + text
//   - near miss:   `4.2` (no exact)  → 404, detail.suggestions includes "4.2.1"
//   - far miss:    `Schedule 999`    → 404, detail.suggestions is an array
//                                       (possibly empty) and crucially the
//                                       endpoint does NOT 500
//
// The third assertion is the core regression guard: before ABS-261 the
// service would still 404 here (the catch translated ValueError → 404),
// but the new shape with `suggestions` in the detail is what the agent
// uses to stop thrashing. We assert the array exists.

import { execSync } from "node:child_process";
import * as path from "node:path";

import { test, expect, E2E_API_URL, DEMO_USER_ID } from "../fixtures/test-env";

// The "4.2" citation_path these tests exercise is owned by the Coverage
// E2E Bylaw, seeded by scripts/seed_e2e_verify_coverage.py. That seed is
// otherwise only run from verify-coverage.spec.ts's beforeAll. Because
// Playwright shards spec FILES across parallel workers, this spec can run
// in a worker where verify-coverage's beforeAll hasn't executed yet — in
// which case "4.2" doesn't exist, the exact-hit lookup 404s, and the
// suggestions array comes back empty (no citation_paths in scope). Seeding
// our own dependency here makes the spec self-contained and deterministic
// regardless of worker scheduling. The seed is idempotent (get-or-create
// by file_hash), so running it here AND in verify-coverage.spec.ts is safe.
test.beforeAll(() => {
  const repoRoot = path.resolve(__dirname, "..", "..", "..");
  const venvPython = path.join(repoRoot, ".venv", "bin", "python");
  // ABS-207: honor PG_PORT so the seed lands in the right Postgres when a
  // parallel worktree runs on a non-default port triplet.
  const pgPort = process.env.PG_PORT || "5433";
  const databaseUrl =
    process.env.DATABASE_URL ||
    `postgresql+psycopg://layer1:layer1@localhost:${pgPort}/layer1_test`;
  const env = {
    ...process.env,
    DATABASE_URL: databaseUrl,
    PYTHONPATH: `${path.join(repoRoot, "src")}:${process.env.PYTHONPATH || ""}`,
  };
  execSync(
    `"${venvPython}" "${path.join(repoRoot, "scripts", "seed_e2e_verify_coverage.py")}"`,
    { env, encoding: "utf-8" },
  );
});

/** Hit the FastAPI /v1/citation endpoint directly with X-Test-User-Id
 *  (same auth shape ABS-9 uses for /v1/chat). Bypassing the Next proxy
 *  is correct here because the bug being regressed lives in the FastAPI
 *  service layer; the proxy just forwards body + status. */
async function fetchCitation(
  request: import("@playwright/test").APIRequestContext,
  citationPath: string,
) {
  return await request.get(
    `${E2E_API_URL}/v1/citation?citation_path=${encodeURIComponent(citationPath)}`,
    {
      headers: { "X-Test-User-Id": DEMO_USER_ID },
    },
  );
}

test.describe("ABS-261: lookup_citation returns suggestions instead of raising", () => {
  test("exact citation_path hit returns 200 with match payload", async ({
    request,
  }) => {
    // "4.2" is carried by the retrieval-enabled e2e seed docs (Coverage
    // E2E Bylaw et al. — since ABS-413 the scope is the enabled-documents
    // set, not the latest ingest). If this fails, the seed didn't run — see
    // scripts/seed_e2e_evaluator_bylaws.py / seed_e2e_verify_coverage.py.
    const res = await fetchCitation(request, "4.2");
    expect(
      res.status(),
      `unexpected status; body: ${await res.text()}`,
    ).toBe(200);
    const body = (await res.json()) as {
      citation_path?: string;
      text?: string;
    };
    expect(body.citation_path).toBe("4.2");
    expect(body.text).toBeTruthy();
  });

  test("near-miss citation_path returns 404 with suggestions including the canonical form", async ({
    request,
  }) => {
    // The e2e seed has citation_path="4.2" stored. A human (or an LLM
    // mid-conversation) would naturally probe with the prose form
    // "Section 4.2" — that's exactly the formatting drift that drove
    // the original max_iterations thrash in the ABS-260 sweep.
    // Pre-fix this 404'd with a string detail; post-fix it 404s with
    // a detail object that carries the suggestions array, with the
    // canonical "4.2" ranked at the top.
    const res = await fetchCitation(request, "Section 4.2");
    expect(res.status()).toBe(404);
    const body = (await res.json()) as {
      detail?: { message?: string; suggestions?: string[] };
    };
    // The detail must be the new structured shape.
    expect(
      body.detail,
      `detail was not an object: ${JSON.stringify(body)}`,
    ).toBeDefined();
    expect(typeof body.detail).toBe("object");
    expect(body.detail?.message).toMatch(/not found/i);
    expect(Array.isArray(body.detail?.suggestions)).toBe(true);
    // The closest stored path must surface. We assert containment rather
    // than rank-1 to keep this resilient to rapidfuzz scorer tweaks
    // and to potential reshuffling between Evaluator / Coverage / etc.
    // e2e seed docs.
    expect(body.detail?.suggestions).toContain("4.2");
  });

  test("far-miss citation_path returns 404 with a suggestions array (no 500, no raise)", async ({
    request,
  }) => {
    // "Schedule 999" has no real overlap with the seeded paths. The
    // critical guarantee here is that the request completes — pre-fix
    // the service raised ValueError and the route translated to 404 with
    // a bare string detail. Now we expect a structured 404 envelope
    // whose suggestions field is at minimum a (possibly empty) array.
    // This is what stops the agent's tool-use loop from thrashing.
    const res = await fetchCitation(request, "Schedule 999");
    expect(res.status()).toBe(404);
    expect(res.status()).not.toBe(500);
    const body = (await res.json()) as {
      detail?: { message?: string; suggestions?: string[] };
    };
    expect(body.detail).toBeDefined();
    expect(typeof body.detail).toBe("object");
    expect(Array.isArray(body.detail?.suggestions)).toBe(true);
    // suggestions MAY include weak partial matches; that's fine. The
    // contract is "an array exists" — that's the signal the agent uses
    // to decide whether to retry lookup_citation or switch to search.
  });
});
