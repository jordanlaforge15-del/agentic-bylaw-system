// ABS-500: dimensional retrieval — a table cell is ranked, and a clause is
// scored under the zone its chapter declares.
//
// RetrievalService.search ranked source_fragment rows and nothing else. Tables
// reached the model only as related_tables attached to a fragment that had
// ALREADY ranked, so a standard stated in a matrix was reachable only if some
// neighbouring prose happened to rank first on its own words. On the ABS-486
// set the dimensional class sat at Recall@10 = 0.056, and every arm of
// ABS-494's fusion matrix left it there — because the defect was inside one
// channel, not in how the channels were weighted against each other.
//
// Reading the labels showed the other half: 17 of the 18 dimensional questions
// are answered by a PROSE section, and those sections never name the zone. The
// by-law declares it once, in the chapter heading, while dozens of unrelated
// clauses list the same zone among abutting land. The scorer paid +4 for the
// passing mention and +2 for the governing chapter, so the ranking had the
// evidence backwards.
//
// This spec drives the real scorer at the FastAPI <-> Postgres boundary against
// a probe corpus seeded by scripts/seed_e2e_abs500_table_channel.py, whose
// docstring lays out both shapes. Every search is scoped by bylaw_name to the
// probe document, so no other seeded corpus can contribute matches.

import { execSync } from "node:child_process";
import * as path from "node:path";

import { test, expect, E2E_API_URL, DEMO_USER_ID } from "../fixtures/test-env";

const BYLAW_NAME = "Dimensional Retrieval Probe Bylaw (ABS-500 E2E)";

// Answered by a cell reading "12.0 metres". The cell's text shares no term
// with the question and says "HR-2" nowhere — its row is identified by a
// table_axis_binding.
const TABLE_QUERY = "What is the maximum building height in the HR-2 zone?";
// Answered by a section whose chapter, not whose text, names the zone.
const PROSE_QUERY = "What is the maximum required lot coverage in the ER-1 zone?";

const INTRO_PATH = "Part V > 94";
const LOADING_PATH = "Part X > 203";
const GOVERNED_PATH = "Part VI > 231";
const MENTIONING_PATH = "Part X > 425";

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
    `"${venvPython}" "${path.join(repoRoot, "scripts", "seed_e2e_abs500_table_channel.py")}"`,
    { env, encoding: "utf-8" },
  );
});

type TableCellMatch = {
  table_id?: number;
  caption?: string | null;
  profile_type?: string | null;
  anchor_fragment_id?: number;
  citation_path?: string | null;
  citation_label?: string | null;
  page_start?: number;
  page_end?: number;
  row_index?: number;
  col_index?: number;
  row_label?: string | null;
  col_label?: string | null;
  text?: string;
  bound_by?: string[];
};

type Match = {
  fragment_id?: number;
  citation_path?: string;
  text?: string;
  score?: number;
  retrieval_channels?: string[];
  table_matches?: TableCellMatch[];
  related_tables?: unknown[];
};

async function search(
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
  expect(
    resp.ok(),
    `search-evidence-raw failed: ${resp.status()} ${await resp.text()}`,
  ).toBeTruthy();
  const body = (await resp.json()) as { matches?: Match[] };
  return body.matches ?? [];
}

function rankOf(matches: Match[], citationPath: string): number {
  return matches.findIndex((m) => m.citation_path === citationPath);
}

function firstTableMatch(matches: Match[]): Match | undefined {
  return matches.find(
    (m) => (m.retrieval_channels ?? []).includes("table") && (m.table_matches ?? []).length > 0,
  );
}

// DoD: a dimensional question whose answer is a table cell retrieves that cell
// without depending on a neighbouring prose fragment ranking first. The
// introducing provision — "Every main building shall comply with the following
// requirements:" — contains no height, no zone and no number, so under the old
// fragment-only ranking there was no route to the cell at all.
test("a dimensional answer that lives in a table cell is retrieved", async ({
  request,
}) => {
  const matches = await search(request, TABLE_QUERY);
  const surfaced = firstTableMatch(matches);

  expect(
    surfaced,
    `no match for "${TABLE_QUERY}" came from the table channel. The answer ` +
      "is a cell, and the provision that introduces the table shares no term " +
      "with the question — so a fragment-only ranker cannot reach it.",
  ).toBeDefined();

  const cell = surfaced!.table_matches![0];
  expect(cell.text).toBe("12.0 metres");
  expect(surfaced!.retrieval_channels).toContain("table");
});

// The binding is what picks the row, not the keywords: "12.0 metres" shares no
// term with the query, and both rows sit under identically-worded headers. One
// query term apart, a different answer.
test("the axis binding selects the zone's row, not a sibling's", async ({
  request,
}) => {
  const cell = firstTableMatch(await search(request, TABLE_QUERY))!.table_matches![0];
  expect(cell.row_label).toBe("HR-2 Zone");
  expect(cell.col_label).toBe("Maximum Building Height");
  expect(cell.bound_by).toEqual(["row bound to zone 'HR-2'"]);

  const sibling = firstTableMatch(
    await search(request, "What is the maximum building height in the HR-1 zone?"),
  )!.table_matches![0];
  expect(sibling.row_label).toBe("HR-1 Zone");
  expect(sibling.text).toBe("8.0 metres");
});

// The citation shape, over the wire. A cell is not a source_fragment, so it is
// cited through the provision that introduces its table and addressed by the
// labels that name it — the contract in docs/ABS-500-TABLE-CHANNEL.md. The
// enclosing match stays fragment-shaped so existing consumers keep working.
test("a ranked cell is cited through the provision that introduces its table", async ({
  request,
}) => {
  const surfaced = firstTableMatch(await search(request, TABLE_QUERY))!;
  const cell = surfaced.table_matches![0];

  expect(cell.citation_path).toBe(INTRO_PATH);
  expect(cell.citation_label).toBe("94");
  expect(cell.anchor_fragment_id).toBe(surfaced.fragment_id);
  expect(cell.profile_type).toBe("dimensional_matrix");
  expect(cell.row_index).toBe(2);
  expect(cell.col_index).toBe(1);
  expect(cell.page_start).toBe(1);
  expect(surfaced.citation_path).toBe(INTRO_PATH);
});

// 63 of the 96 tables in the dev corpus carry no parent_fragment_id, and every
// table in the Halifax Mainland by-law does. The anchor is then the fragment
// immediately before the table in the parser's block ordering.
test("a parentless table is still citable, through the reading order", async ({
  request,
}) => {
  const surfaced = firstTableMatch(
    await search(request, "What is the minimum loading space length in the CH-1 zone?"),
  );
  expect(
    surfaced,
    "the parentless loading table did not rank; its anchor could not be " +
      "recovered from source_block_ids_json.",
  ).toBeDefined();
  expect(surfaced!.table_matches![0].citation_path).toBe(LOADING_PATH);
  expect(surfaced!.table_matches![0].text).toBe("9.0 metres");
});

// The evidence must not be gated behind a display flag — which is the bug this
// issue was opened about, one level down. include_tables governs the bulk
// related_tables dump (everything near the fragment, query-relevant or not);
// the cell that MADE the match rank is its citation, and a match without it is
// not groundable. search-evidence-raw builds a RetrievalRequest with no
// include_* keys, so include_tables is at its model default of False here.
test("the cell that earned the rank travels with the match", async ({ request }) => {
  const surfaced = firstTableMatch(await search(request, TABLE_QUERY))!;
  expect(surfaced.table_matches!.length).toBeGreaterThan(0);
  expect(surfaced.related_tables ?? []).toEqual([]);
});

// The prose half, and the inversion that actually held the dimensional class
// at 0.056. The governed section does not contain "ER-1" anywhere; the
// mentioning clause contains it in its own text. Under own-text-plus-context
// scoring alone the mentioning clause wins, which is what the real corpus did.
test("a clause governed by a zone chapter outranks one that merely names the zone", async ({
  request,
}) => {
  const matches = await search(request, PROSE_QUERY);
  const governed = rankOf(matches, GOVERNED_PATH);
  const mentioning = rankOf(matches, MENTIONING_PATH);

  expect(governed, `${GOVERNED_PATH} missing from the ranking`).toBeGreaterThanOrEqual(0);
  expect(
    mentioning,
    `${MENTIONING_PATH} missing from the ranking`,
  ).toBeGreaterThanOrEqual(0);

  const governedMatch = matches[governed];
  expect(
    governedMatch.text ?? "",
    "fixture drift: the governed section is supposed to state the standard " +
      "without naming the zone — that is the whole point of the test.",
  ).not.toMatch(/ER-1/i);

  expect(
    governed,
    "the clause that merely lists ER-1 among abutting zones outranked the " +
      "section governed by the ER-1 chapter: zone-scope binding is not firing " +
      "(see bylaw_retrieval/retrieval/binding.py).",
  ).toBeLessThan(mentioning);
});

// The guard that keeps the binding from becoming noise: with no zone in the
// query there is no scope to bind to, and the fragment that actually says
// something about landscaped buffers wins on its own words.
test("a query naming no zone binds nothing", async ({ request }) => {
  const matches = await search(request, "When is a landscaped buffer required?");
  expect(matches[0]?.citation_path).toBe(MENTIONING_PATH);
});
