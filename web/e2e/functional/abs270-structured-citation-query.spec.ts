// ABS-270 Phase 1A: structured query support on lookup_citation.
//
// The ``CitationLookupRequest`` schema now accepts a ``structured`` field
// (``ZoneAttributeQuery`` or ``ScheduleRowQuery``) as an alternative to the
// free-text ``citation_path`` string. The service's ``_lookup_via_structured``
// method maps the structured variant to a canonical path (e.g.
// ``"HR-2 > max_height"``) and delegates to the existing path-string lookup.
// Miss handling (ABS-261 suggestions envelope) is preserved end-to-end.
//
// This spec drives the ``/v1/_test/lookup-citation`` endpoint (mounted only
// on the e2e test server) so no chat-session / mock-dispatcher machinery
// is needed. The endpoint exposes the same service path the LLM tool loop
// uses when it calls ``lookup_citation`` with the ``structured`` field.
//
// Seeded data (``scripts/seed_e2e_abs270_structured.py``):
//   - fragment ``citation_path="HR-2 > max_height"``   (ZoneAttributeQuery hit)
//   - fragment ``citation_path="HR-2 > max_lot_coverage"``  (near-miss for suggestions)
//   - fragment ``citation_path="Table 1A > HR-2"``  (ScheduleRowQuery hit)
//
// Test matrix:
//   1. ZoneAttributeQuery hit → 200, match populated
//   2. ZoneAttributeQuery miss (unknown zone) → 200, match=null, suggestions array
//   3. Unknown attribute → 422 validation error
//   4. ScheduleRowQuery hit → 200, match populated
//   5. Both citation_path + structured supplied → 422 validation error
//   6. Neither citation_path nor structured supplied → 422 validation error

import { execSync } from "node:child_process";
import * as path from "node:path";

import { E2E_API_URL, expect, test } from "../fixtures/test-env";


const ABS270_BYLAW_NAME = "ABS-270 Structured Query E2E Bylaw";


function runSeed(): void {
  const repoRoot = path.resolve(__dirname, "..", "..", "..");
  const venvPython = path.join(repoRoot, ".venv", "bin", "python");
  const pgPort = process.env.PG_PORT || "5432";
  const databaseUrl =
    process.env.DATABASE_URL ||
    `postgresql+psycopg://layer1:layer1@localhost:${pgPort}/layer1_test`;
  execSync(
    `"${venvPython}" "${path.join(repoRoot, "scripts", "seed_e2e_abs270_structured.py")}"`,
    {
      env: {
        ...process.env,
        DATABASE_URL: databaseUrl,
        PYTHONPATH: `${path.join(repoRoot, "src")}:${process.env.PYTHONPATH || ""}`,
      },
      stdio: "inherit",
    },
  );
}


test.beforeAll(() => {
  runSeed();
});


test.afterAll(async ({ request }) => {
  // Remove the seeded document so it doesn't pollute the suggestion
  // corpus for other concurrently-running specs that rely on known
  // citation_path candidates (e.g. abs261-citation-suggestions.spec.ts).
  await request.post(`${E2E_API_URL}/v1/_test/delete-documents`, {
    headers: { "Content-Type": "application/json" },
    data: { bylaw_name: ABS270_BYLAW_NAME },
  });
});


/** Hit the test-only lookup-citation endpoint. */
async function lookupCitation(
  request: import("@playwright/test").APIRequestContext,
  body: Record<string, unknown>,
): Promise<{ status: number; json: unknown }> {
  const res = await request.post(`${E2E_API_URL}/v1/_test/lookup-citation`, {
    headers: { "Content-Type": "application/json" },
    data: body,
  });
  return { status: res.status(), json: await res.json() };
}


test.describe("ABS-270: structured query support on lookup_citation", () => {
  test("ZoneAttributeQuery hit — match is populated with zone fragment text", async ({
    request,
  }) => {
    const { status, json } = await lookupCitation(request, {
      structured: { kind: "zone_attribute", zone: "HR-2", attribute: "max_height" },
    });
    expect(
      status,
      `unexpected status; body: ${JSON.stringify(json)}`,
    ).toBe(200);
    const body = json as { match: { citation_path?: string; text?: string } | null; suggestions: string[] };
    expect(body.match, "match should be populated on a hit").not.toBeNull();
    expect(body.match?.citation_path).toBe("HR-2 > max_height");
    expect(body.match?.text).toMatch(/HR-2/);
    expect(body.suggestions).toHaveLength(0);
  });


  test("ZoneAttributeQuery miss — match=null, suggestions array returned", async ({
    request,
  }) => {
    // Zone "ZZZZ-999" does not exist; the canonical path "ZZZZ-999 > max_height"
    // has no stored fragment. The service must return match=null + a suggestions
    // array (possibly empty) rather than raising — same ABS-261 contract.
    const { status, json } = await lookupCitation(request, {
      structured: { kind: "zone_attribute", zone: "ZZZZ-999", attribute: "max_height" },
    });
    expect(
      status,
      `unexpected status; body: ${JSON.stringify(json)}`,
    ).toBe(200);
    const body = json as { match: unknown; suggestions: string[] };
    expect(body.match).toBeNull();
    expect(Array.isArray(body.suggestions)).toBe(true);
    // Should NOT raise — the array may be empty or contain near matches.
  });


  test("ZoneAttributeQuery miss with known-zone — suggestions include near-match paths", async ({
    request,
  }) => {
    // Zone "HR-2" exists (citation_paths include "HR-2 > max_height" and
    // "HR-2 > max_lot_coverage"). Querying for "HR-2 > min_front_setback"
    // (a path not seeded) should return suggestions that include other HR-2 paths.
    const { status, json } = await lookupCitation(request, {
      structured: { kind: "zone_attribute", zone: "HR-2", attribute: "min_front_setback" },
    });
    expect(status).toBe(200);
    const body = json as { match: unknown; suggestions: string[] };
    expect(body.match).toBeNull();
    expect(Array.isArray(body.suggestions)).toBe(true);
    // At least one HR-2 path should surface as a suggestion.
    const hasHR2Suggestion = body.suggestions.some((s) => s.startsWith("HR-2"));
    expect(hasHR2Suggestion).toBe(true);
  });


  test("unknown attribute — 422 validation error", async ({ request }) => {
    // The ATTRIBUTE_VOCABULARY rejects attributes not in the accepted list
    // immediately (before any DB hit), so the model gets a tight error message
    // naming the accepted values rather than an empty suggestion envelope.
    const { status, json } = await lookupCitation(request, {
      structured: { kind: "zone_attribute", zone: "HR-2", attribute: "invalid_attr_xyz" },
    });
    expect(status).toBe(422);
    // The body should be an error shape, not a CitationLookupResponse.
    const body = json as Record<string, unknown>;
    expect(body).not.toHaveProperty("match");
  });


  test("ScheduleRowQuery hit — match is populated with schedule row text", async ({
    request,
  }) => {
    const { status, json } = await lookupCitation(request, {
      structured: { kind: "schedule_row", schedule: "Table 1A", row: "HR-2" },
    });
    expect(
      status,
      `unexpected status; body: ${JSON.stringify(json)}`,
    ).toBe(200);
    const body = json as { match: { citation_path?: string; text?: string } | null; suggestions: string[] };
    expect(body.match, "match should be populated on a schedule_row hit").not.toBeNull();
    expect(body.match?.citation_path).toBe("Table 1A > HR-2");
    expect(body.match?.text).toMatch(/Table 1A/i);
    expect(body.suggestions).toHaveLength(0);
  });


  test("ScheduleRowQuery miss — match=null, suggestions array returned", async ({
    request,
  }) => {
    const { status, json } = await lookupCitation(request, {
      structured: { kind: "schedule_row", schedule: "Schedule 99", row: "ZZZ-999" },
    });
    expect(status).toBe(200);
    const body = json as { match: unknown; suggestions: string[] };
    expect(body.match).toBeNull();
    expect(Array.isArray(body.suggestions)).toBe(true);
  });


  test("both citation_path and structured supplied — 422 validation error", async ({
    request,
  }) => {
    // The @model_validator on CitationLookupRequest rejects requests that
    // supply both — mutually exclusive fields.
    const { status, json } = await lookupCitation(request, {
      citation_path: "4.2",
      structured: { kind: "zone_attribute", zone: "HR-2", attribute: "max_height" },
    });
    expect(status).toBe(422);
    const body = json as Record<string, unknown>;
    expect(body).not.toHaveProperty("match");
  });


  test("neither citation_path nor structured supplied — 422 validation error", async ({
    request,
  }) => {
    const { status, json } = await lookupCitation(request, {});
    expect(status).toBe(422);
    const body = json as Record<string, unknown>;
    expect(body).not.toHaveProperty("match");
  });
});
