// ABS-492: provision-in-context — a clause is scored under the scope its
// containers supply.
//
// A by-law clause is written to be read under its heading. Retrieval only ever
// read the leaf, so "(f) 2.5 metres elsewhere." was a five-token fragment with
// no visible scope: the zone it applies in sits two levels up, the dimension it
// constrains one level up, and neither was indexed or scored.
//
// The inverse failure came from the same blind spot. ABS-488 repathed clauses
// onto the container that scopes them, folding the container's whole sentence
// into the child's citation_path as a bracketed segment — and that sentence was
// scored at *path* weight, +35 for the phrase and +12 a token, against +4 for
// the fragment's own text. So a clause about a special area outranked the
// section that states the setback standard.
//
// This spec drives the real scorer at the FastAPI ↔ Postgres boundary against a
// probe corpus seeded by scripts/seed_e2e_abs492_provision_context.py, whose
// docstring lays out the tree. Two endpoints, deliberately:
//
//   /v1/_test/search-evidence-raw   builds a RetrievalRequest directly — the
//                                   ranking assertions.
//   /v1/_test/openai-tool-search    goes through OpenAIToolExecutor, the path
//                                   an LLM's tool call actually takes, with no
//                                   include_* arguments — the assertion that
//                                   the scope which earned the rank comes back
//                                   with the match.
//
// Every search is scoped by bylaw_name to the probe document, so no other
// seeded corpus can contribute matches.

import { execSync } from "node:child_process";
import * as path from "node:path";

import { test, expect, E2E_API_URL, DEMO_USER_ID } from "../fixtures/test-env";

const BYLAW_NAME = "Provision Context Probe Bylaw (ABS-492 E2E)";

// Only the chapter names "ER-3"; only the section names "side setback"; the
// list item names neither.
const PROBE_QUERY = "ER-3 side setback";

const CHAPTER_PATH = "Part V, Chapter 9";
const SECTION_PATH = "Part V > 229";
const LIST_ITEM_PATH = "Part V > 229 > (f)";
const DECOY_PATH =
  "Part V > 135 > [The maximum required side setback for any main building shall be] > (a)";
const TWIN_PATH = "Part IX > 631";

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
    `"${venvPython}" "${path.join(repoRoot, "scripts", "seed_e2e_abs492_provision_context.py")}"`,
    { env, encoding: "utf-8" },
  );
});

type Ancestor = {
  citation_path?: string | null;
  citation_label?: string | null;
  text?: string;
};

type Match = {
  citation_path?: string;
  text?: string;
  score?: number;
  ancestor_chain?: Ancestor[];
};

async function search(
  request: import("@playwright/test").APIRequestContext,
  endpoint: "search-evidence-raw" | "openai-tool-search",
  query: string,
): Promise<Match[]> {
  const resp = await request.post(`${E2E_API_URL}/v1/_test/${endpoint}`, {
    headers: {
      "Content-Type": "application/json",
      "X-Test-User-Id": DEMO_USER_ID,
    },
    data: { query, bylaw_name: BYLAW_NAME, limit: 10 },
  });
  expect(resp.ok(), `${endpoint} failed: ${resp.status()} ${await resp.text()}`).toBeTruthy();
  const body = (await resp.json()) as { matches?: Match[] };
  return body.matches ?? [];
}

function rankOf(matches: Match[], citationPath: string): number {
  return matches.findIndex((m) => m.citation_path === citationPath);
}

// DoD: the stripped list item is retrievable through its parent section's
// terms. Nothing in "(f) 2.5 metres elsewhere." matches any word of the query;
// the only route to it is the chapter that names the zone and the section that
// names the dimension.
test("a stripped list item is retrievable through its containers' terms", async ({
  request,
}) => {
  const matches = await search(request, "search-evidence-raw", PROBE_QUERY);
  const listItem = matches.find((m) => m.citation_path === LIST_ITEM_PATH);

  expect(
    listItem,
    `"${LIST_ITEM_PATH}" did not surface for "${PROBE_QUERY}". Its own text ` +
      "contains no term of the query, so this is the context channel failing " +
      "to reach it through its ancestor chain.",
  ).toBeDefined();
  expect(listItem?.text ?? "").not.toMatch(/er-3|side|setback/i);
});

// Inherited scope must not invert the tree. Both fragments sit under the
// chapter that names the zone, so both inherit "ER-3" — but only the section
// states the dimension, and it has to stay ahead of the clause it introduces.
test("the section stating the standard still outranks its own list item", async ({
  request,
}) => {
  const matches = await search(request, "search-evidence-raw", PROBE_QUERY);
  const section = rankOf(matches, SECTION_PATH);
  const listItem = rankOf(matches, LIST_ITEM_PATH);

  expect(section, `${SECTION_PATH} missing from the ranking`).toBeGreaterThanOrEqual(0);
  expect(listItem, `${LIST_ITEM_PATH} missing from the ranking`).toBeGreaterThanOrEqual(0);
  expect(section).toBeLessThan(listItem);
});

// The ABS-488 inversion, stated as a ranking. The decoy's own text is about a
// special area; every query term it scores on lives in the container sentence
// folded into its path. At path weight that was +35 for the phrase and +12 a
// token — 59 against the section's 9 — and it topped the ranking. The prose is
// still worth something (its container really does say "side setback"), but at
// context weight, which puts it last: below the section that states the rule,
// below the chapter that scopes it, and below the list item the rule belongs to.
test("container prose folded into a citation path no longer outranks the rule", async ({
  request,
}) => {
  const matches = await search(request, "search-evidence-raw", PROBE_QUERY);
  const decoy = rankOf(matches, DECOY_PATH);

  expect(decoy, `${DECOY_PATH} missing from the ranking`).toBeGreaterThanOrEqual(0);
  expect(
    decoy,
    "the decoy outranked a fragment that states or is scoped by the rule: " +
      "bracketed container prose is being scored at path weight again (see " +
      "score_fragment_detail in mcp/bylaw_retrieval/retrieval/service.py).",
  ).toBeGreaterThan(rankOf(matches, SECTION_PATH));
  expect(decoy).toBeGreaterThan(rankOf(matches, CHAPTER_PATH));
  expect(decoy).toBeGreaterThan(rankOf(matches, LIST_ITEM_PATH));
});

// The control that separates "inherited scope" from "longer text": the twin
// repeats the section's text word for word with no container at all, so the
// gap between them is the chapter and nothing else.
test("scope from the chapter is what lifts the section above its twin", async ({
  request,
}) => {
  const matches = await search(request, "search-evidence-raw", PROBE_QUERY);
  const section = matches.find((m) => m.citation_path === SECTION_PATH);
  const twin = matches.find((m) => m.citation_path === TWIN_PATH);

  expect(section, `${SECTION_PATH} missing`).toBeDefined();
  expect(twin, `${TWIN_PATH} missing`).toBeDefined();
  expect(section!.text).toBe(twin!.text);
  expect(section!.score ?? 0).toBeGreaterThan(twin!.score ?? 0);
});

// A query whose terms the fragment states itself gains nothing from its
// ancestors — the paired negative for the test above, and the guard against the
// context channel becoming a constant added to everything with a parent.
test("a term the fragment already states is not paid for twice", async ({
  request,
}) => {
  const matches = await search(request, "search-evidence-raw", "side setback");
  const section = matches.find((m) => m.citation_path === SECTION_PATH);
  const twin = matches.find((m) => m.citation_path === TWIN_PATH);

  expect(section, `${SECTION_PATH} missing`).toBeDefined();
  expect(twin, `${TWIN_PATH} missing`).toBeDefined();
  expect(section!.score).toBe(twin!.score);
});

// The scope that earned the rank has to travel with the match. An LLM calling
// the tool never sets include_* — no persona tells it to — so the tool surface
// defaults it on. Without the chain the model receives "(f) 2.5 metres
// elsewhere." with no way to see which zone or dimension it governs, and the
// higher rank becomes a liability rather than a help.
test("the tool surface returns the ancestor chain that earned the rank", async ({
  request,
}) => {
  const matches = await search(request, "openai-tool-search", PROBE_QUERY);
  const listItem = matches.find((m) => m.citation_path === LIST_ITEM_PATH);

  expect(listItem, `${LIST_ITEM_PATH} missing from the tool response`).toBeDefined();

  const chain = listItem!.ancestor_chain ?? [];
  expect(
    chain.length,
    "ABS-492 drift: search_bylaw_evidence returned a context-ranked match with " +
      "no ancestor_chain. The OpenAI tool surface must default include_context " +
      "on (see _TOOL_SURFACE_INCLUDE_DEFAULTS in mcp/bylaw_retrieval/openai_tools.py).",
  ).toBeGreaterThan(0);

  const chainPaths = chain.map((a) => a.citation_path);
  expect(chainPaths).toContain(CHAPTER_PATH);
  expect(chainPaths).toContain(SECTION_PATH);
  // Root first: the reader needs the zone before the clause.
  expect(chainPaths.indexOf(CHAPTER_PATH)).toBeLessThan(
    chainPaths.indexOf(SECTION_PATH),
  );
  expect(chain[0].text).toContain("ER-3");
});
