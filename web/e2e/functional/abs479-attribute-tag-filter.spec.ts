// ABS-479: attribute_tag_filter reaches the product surface.
//
// The GIN index on ``source_fragment.attribute_tags`` (migration 0014) is the
// only indexed retrieval pre-filter in the repo. Until this issue it was
// reachable only from the compliance evaluator — the MCP server accepted the
// argument but the chat/OpenAI tool schemas never advertised it, so no LLM
// could ask for it.
//
// Pytest pins each link of the chain in isolation against sqlite. This spec
// runs the whole chain — chat tool schema → handler → RetrievalRequest →
// indexed ``attribute_tags`` clause — inside the running FastAPI process
// against the real Postgres, via
// ``POST /v1/_test/advisor-search-attribute-tag-filter`` (which drives the
// production ``search_bylaw_evidence`` handler from ``advisor.chat.tools``).
//
// Why the real stack matters here: the service takes a DIFFERENT code path per
// dialect. Postgres uses the JSONB ``?|`` operator against the GIN index;
// sqlite falls back to LIKE-matching the JSON text. A unit suite can only ever
// exercise the fallback, so a broken ``?|`` bind type (or a missing migration
// 0014) would pass pytest and fail in production. That regression has already
// happened once on this code — see the ABS-46 note in
// ``_attribute_tag_filter_clause``.
//
// Coverage:
//   * filter narrows the result set to the tagged clauses (the indexed path)
//   * multiple IDs union (any-of, not all-of)
//   * a valid-but-unused taxonomy ID returns zero matches, not everything
//   * an EMPTY list is rejected as a tool error the model can read — not a
//     500, and not a silent "no filter"
//
// Data dependency: ``scripts/seed_e2e_evaluator_bylaws.py`` (idempotent), whose
// three clauses carry exactly the tags asserted on below:
//   §4.2.1 [front_setback_m]  §4.3.1 [building_height_m]
//   §4.4.1 [corner_lot_boolean, front_setback_m]

import { execSync } from "node:child_process";
import * as path from "node:path";

import { test, expect, E2E_API_URL, DEMO_USER_ID } from "../fixtures/test-env";

const BYLAW_NAME = "Evaluator E2E Bylaw";
// Every seeded clause contains "metres", so the bare text query matches all
// three. Anything the filter removes was removed by the tag clause, not by
// text scoring — that's what makes the narrowing assertions meaningful.
const QUERY = "minimum front yard metres height";

type FilterResponse = {
  ok: boolean;
  error?: string;
  result?: { matches: Array<{ citation_path: string | null }> };
};

test.beforeAll(() => {
  const repoRoot = path.resolve(__dirname, "..", "..", "..");
  const venvPython = path.join(repoRoot, ".venv", "bin", "python");
  const pgPort = process.env.PG_PORT || "5433";
  const databaseUrl =
    process.env.DATABASE_URL ||
    `postgresql+psycopg://layer1:layer1@localhost:${pgPort}/layer1_test`;
  execSync(
    `"${venvPython}" "${path.join(repoRoot, "scripts", "seed_e2e_evaluator_bylaws.py")}"`,
    {
      env: {
        ...process.env,
        DATABASE_URL: databaseUrl,
        PYTHONPATH: `${path.join(repoRoot, "src")}:${process.env.PYTHONPATH || ""}`,
      },
      encoding: "utf-8",
    },
  );
});

async function search(
  request: import("@playwright/test").APIRequestContext,
  body: Record<string, unknown>,
): Promise<FilterResponse> {
  const response = await request.post(
    `${E2E_API_URL}/v1/_test/advisor-search-attribute-tag-filter`,
    {
      headers: {
        "Content-Type": "application/json",
        "X-Test-User-Id": DEMO_USER_ID,
      },
      data: { query: QUERY, bylaw_name: BYLAW_NAME, limit: 20, ...body },
    },
  );
  expect(
    response.status(),
    `endpoint failed: ${response.status()} ${await response.text()}`,
  ).toBe(200);
  return (await response.json()) as FilterResponse;
}

function citations(body: FilterResponse): Set<string | null> {
  expect(body.ok, `expected a successful tool result, got: ${body.error}`).toBe(true);
  return new Set((body.result?.matches ?? []).map((m) => m.citation_path));
}

test("unfiltered search matches every seeded clause (fixture precondition)", async ({
  request,
}) => {
  const found = citations(await search(request, {}));
  for (const citation of ["4.2.1", "4.3.1", "4.4.1"]) {
    expect(
      found,
      `bare query must match ${citation} for the filter tests to mean anything`,
    ).toContain(citation);
  }
});

test("attribute_tag_filter narrows results through the indexed clause", async ({
  request,
}) => {
  const found = citations(
    await search(request, { attribute_tag_filter: ["building_height_m"] }),
  );
  // Only §4.3.1 carries building_height_m. §4.2.1 / §4.4.1 match the text
  // just as well but are not tagged, so the pre-filter must drop them.
  expect(found).toEqual(new Set(["4.3.1"]));
});

test("multiple attribute IDs union rather than intersect", async ({ request }) => {
  const found = citations(
    await search(request, {
      attribute_tag_filter: ["front_setback_m", "building_height_m"],
    }),
  );
  // front_setback_m alone covers §4.2.1 and §4.4.1; adding building_height_m
  // adds §4.3.1. An intersection semantic would return nothing.
  expect(found).toEqual(new Set(["4.2.1", "4.3.1", "4.4.1"]));
});

test("a tag no clause carries returns zero matches, not the whole corpus", async ({
  request,
}) => {
  const found = citations(
    await search(request, { attribute_tag_filter: ["parking_stalls_count"] }),
  );
  // The pre-filter must fail closed. Falling back to "no filter" on an
  // unmatched tag would hand the model clauses that regulate something else.
  expect(found.size).toBe(0);
});

test("an empty attribute_tag_filter is rejected as a tool error, not a 500", async ({
  request,
}) => {
  const body = await search(request, { attribute_tag_filter: [] });
  expect(body.ok).toBe(false);
  // The message is what the model sees in the is_error tool_result, so it has
  // to name the field and the corrective action.
  expect(body.error).toContain("attribute_tag_filter must be non-empty");
  expect(body.error).toContain("omit the field");
});
