// Functional: ABS-517 — eval transcripts must carry tool inputs and results.
//
// The gap this closes:
//   ABS-459 made `tool_calls` in an eval transcript report the calls the
//   loop actually dispatched, by falling back to the `tool_loop_metrics`
//   SSE event. But that event carried only name / error / latency —
//   ABS-266 dropped arguments and outputs on purpose, reasoning that the
//   event was for cost-and-perf observability. So a transcript could say
//   `search_bylaw_evidence` ran 33 times without saying what it was asked
//   or what came back.
//
// Why that blocks work rather than merely being untidy:
//   Five cases in evals/runs/zone-typology-all8 fail by omitting one
//   specific provision each (TC-024 drops the 60 sq m cap of s.333(1)(a),
//   TC-028 drops the 4.5 m stepback, …). Each omission has two possible
//   causes with OPPOSITE fixes — the provision never came back from the
//   tool (fix indexing / query construction) or it came back and the
//   answer dropped it (fix the prompt / tool loop). A name-only transcript
//   cannot tell them apart, so an agent asked to RCA one can only guess,
//   and a wrong guess sends work to the wrong layer.
//
// What is asserted, in two layers:
//   1. Live, through the real stack — drive /v1/chat and decode the
//      `tool_loop_metrics` event off the wire. Every dispatched call must
//      arrive with its `input` and a bounded `result_excerpt`. This is the
//      only assertion that proves the payloads survive Pydantic
//      serialization, `_format_sse_event`, and SSE framing.
//   2. Over committed artifacts — any transcript stamped
//      parser_version >= 3 must honour the payload guarantee, and the
//      runner must still declare that version and map the fields onto the
//      transcript. Layer 2 is vacuous until the first v3 run lands (the
//      shape ABS-459 and ABS-306 established); the runner-source checks
//      are what keep it from going quietly dead in the meantime.
//
//   Calls FastAPI directly rather than the Next.js proxy — same reason as
//   abs291-cache-aware-cost-circuit.spec.ts: the proxy pins X-Test-User-Id.

import { execSync } from "node:child_process";
import * as fs from "node:fs";
import * as path from "node:path";

import { expect, test } from "@playwright/test";

import { E2E_API_URL } from "../fixtures/test-env";
import { resolveDatabaseUrl } from "../helpers/database-url";

const REPO_ROOT = path.resolve(__dirname, "../../../");
const RUNS_DIR = path.join(REPO_ROOT, "evals", "runs");

/** The version that first guarantees per-call payloads. */
const PAYLOAD_PARSER_VERSION = 3;

const TEST_USER_ID = `abs517-${Date.now()}-${Math.random()
  .toString(36)
  .slice(2, 8)}`;

test.beforeAll(() => {
  const seed = path.join(REPO_ROOT, "scripts", "seed_e2e_user.py");
  const venvPython = path.join(REPO_ROOT, ".venv", "bin", "python");
  execSync(
    `"${venvPython}" "${seed}" --user-id "${TEST_USER_ID}" ` +
      `--email "${TEST_USER_ID}@e2e.test" --credits-per-tier 5`,
    {
      env: {
        ...process.env,
        DATABASE_URL: resolveDatabaseUrl(),
        PYTHONPATH: `${path.join(REPO_ROOT, "src")}:${
          process.env.PYTHONPATH || ""
        }`,
      },
      stdio: "inherit",
    },
  );
});

// ---------------------------------------------------------------------------
// Layer 1: the payloads survive the wire
// ---------------------------------------------------------------------------

interface WireToolCall {
  name?: string;
  is_error?: boolean;
  input?: Record<string, unknown> | null;
  result_excerpt?: string | null;
  result_chars?: number | null;
  result_truncated?: boolean;
  result_citations?: string[];
}

interface WireMetrics {
  terminated_reason?: string;
  tool_calls?: WireToolCall[];
}

function isRecord(v: unknown): v is Record<string, unknown> {
  return typeof v === "object" && v !== null;
}

async function openCase(
  request: import("@playwright/test").APIRequestContext,
): Promise<number> {
  const res = await request.post(`${E2E_API_URL}/v1/cases`, {
    headers: { "X-Test-User-Id": TEST_USER_ID },
    data: {
      anchor_label: "100 Robie Street, Halifax (ABS-517)",
      anchor_kind: "address",
      tier: "standard",
    },
  });
  expect(
    res.status(),
    `open_case failed: ${res.status()} ${await res.text()}`,
  ).toBe(200);
  const body = (await res.json()) as { case: { id: number } };
  return body.case.id;
}

/** Send one chat turn and return the decoded `tool_loop_metrics` event. */
async function chatMetrics(
  request: import("@playwright/test").APIRequestContext,
  caseId: number,
  message: string,
): Promise<WireMetrics> {
  const res = await request.post(`${E2E_API_URL}/v1/chat`, {
    headers: {
      "X-Test-User-Id": TEST_USER_ID,
      "Content-Type": "application/json",
      Accept: "text/event-stream",
    },
    data: { message, case_id: caseId, session_id: null },
    timeout: 30_000,
  });
  const body = await res.text();
  expect(res.status(), `chat failed: ${body.slice(0, 400)}`).toBe(200);

  for (const line of body.split(/\r?\n/)) {
    if (!line.startsWith("data: ")) continue;
    const payload = line.slice("data: ".length);
    if (!payload || payload === "[DONE]") continue;
    let parsed: unknown;
    try {
      parsed = JSON.parse(payload);
    } catch {
      continue;
    }
    if (isRecord(parsed) && parsed.type === "tool_loop_metrics") {
      return parsed as WireMetrics;
    }
  }
  throw new Error(
    `tool_loop_metrics event absent from the SSE stream: ${body.slice(0, 400)}`,
  );
}

test("every dispatched tool call arrives with its input and a bounded result", async ({
  request,
}) => {
  const caseId = await openCase(request);
  const metrics = await chatMetrics(
    request,
    caseId,
    "What is the minimum side yard setback?",
  );

  const calls = metrics.tool_calls ?? [];
  expect(
    calls.length,
    "the turn must dispatch at least one tool for this to assert anything",
  ).toBeGreaterThan(0);

  for (const [i, call] of calls.entries()) {
    const where = `tool_calls[${i}] (${call.name})`;

    // The arguments the model passed. Null here is the ABS-517 regression
    // itself — the transcript would again be unable to say what was asked.
    expect(call.input, `${where}: input must be recorded, not null`).not.toBe(
      null,
    );
    expect(typeof call.input, `${where}: input must be an object`).toBe(
      "object",
    );

    // The result. A successful call reports its output; a failed one
    // reports its error text — either way the excerpt is populated, and
    // `is_error` says which one a reader is looking at.
    expect(
      call.result_excerpt,
      `${where}: result_excerpt must be recorded`,
    ).toBeTruthy();
    expect(
      call.result_chars,
      `${where}: result_chars must report the pre-truncation length`,
    ).toBeGreaterThan(0);

    // Bounded: the excerpt is never longer than the full result it came
    // from, and it is flagged when it is a prefix.
    if (call.result_truncated) {
      expect(
        (call.result_excerpt ?? "").length,
        `${where}: a truncated excerpt must be shorter than the full result`,
      ).toBeLessThan(call.result_chars!);
    }
  }

  // The successful search reports a JSON payload from the retrieval
  // handler, not an opaque placeholder — this is the substance an RCA
  // reader actually reads.
  const search = calls.find(
    (c) => c.name === "search_bylaw_evidence" && !c.is_error,
  );
  expect(search, "the setback question must dispatch a bylaw search").toBeDefined();
  expect(search!.input).toHaveProperty("query");
  expect(search!.result_excerpt!.trimStart().startsWith("{")).toBe(true);
});

test("parallel calls in one iteration each carry their own input", async ({
  request,
}) => {
  // MOCK_FAN_OUT makes the dispatcher emit two tool_use blocks in a single
  // assistant response, which the loop executes in parallel. Guards against
  // the payloads being attached per-iteration rather than per-call — an
  // error that would still look correct on a single-call turn but would
  // silently attribute one call's result to another during RCA.
  const caseId = await openCase(request);
  const metrics = await chatMetrics(
    request,
    caseId,
    "MOCK_FAN_OUT height and floor area ratio please",
  );

  const searches = (metrics.tool_calls ?? []).filter(
    (c) => c.name === "search_bylaw_evidence",
  );
  expect(searches.length).toBeGreaterThanOrEqual(2);

  const queries = searches.map((c) => String(c.input?.query ?? ""));
  expect(queries).toContain("maximum building height");
  expect(queries).toContain("maximum floor area ratio");
  expect(
    new Set(queries).size,
    `each parallel call must keep its own input, got ${JSON.stringify(queries)}`,
  ).toBeGreaterThan(1);
});

// ---------------------------------------------------------------------------
// Layer 2: the guarantee holds over committed artifacts
// ---------------------------------------------------------------------------

interface TranscriptToolCall extends WireToolCall {
  source?: string;
}

interface Transcript {
  id: string;
  parser_version?: number;
  turns?: { turn: number; tool_calls?: TranscriptToolCall[] }[];
}

function transcriptsIn(dir: string): { file: string; doc: Transcript }[] {
  if (!fs.existsSync(dir)) return [];
  return fs
    .readdirSync(dir)
    .filter((f) => /^TC-\d{3}\.json$/.test(f))
    .map((f) => ({
      file: path.join(dir, f),
      doc: JSON.parse(fs.readFileSync(path.join(dir, f), "utf-8")) as Transcript,
    }));
}

function allRunDirs(): string[] {
  if (!fs.existsSync(RUNS_DIR)) return [];
  const out: string[] = [];
  const walk = (dir: string, depth: number) => {
    if (depth > 2) return;
    for (const entry of fs.readdirSync(dir, { withFileTypes: true })) {
      if (!entry.isDirectory()) continue;
      const full = path.join(dir, entry.name);
      if (transcriptsIn(full).length > 0) out.push(full);
      else walk(full, depth + 1);
    }
  };
  walk(RUNS_DIR, 0);
  return out;
}

test("no v3 transcript records a tool call without its payload", () => {
  const violations: string[] = [];
  let checked = 0;
  let legacy = 0;

  for (const dir of allRunDirs()) {
    for (const { file, doc } of transcriptsIn(dir)) {
      // Runs captured before ABS-517 genuinely have no payloads to carry;
      // backfilling them is impossible (the data was never emitted).
      // Skipped by version, and counted so the exemption stays visible.
      if ((doc.parser_version ?? 0) < PAYLOAD_PARSER_VERSION) {
        legacy += 1;
        continue;
      }
      checked += 1;
      for (const turn of doc.turns ?? []) {
        for (const [i, call] of (turn.tool_calls ?? []).entries()) {
          const where = `${path.relative(REPO_ROOT, file)} turn ${turn.turn} call ${i}`;
          if (call.input === null || call.input === undefined) {
            violations.push(`${where}: no input recorded`);
          }
          if (!call.result_excerpt && call.result_chars !== 0) {
            violations.push(`${where}: no result_excerpt recorded`);
          }
        }
      }
    }
  }

  console.log(
    `ABS-517 payload guarantee: ${checked} v${PAYLOAD_PARSER_VERSION}+ transcript(s) ` +
      `checked, ${legacy} pre-payload transcript(s) skipped.`,
  );

  expect(
    violations,
    `v${PAYLOAD_PARSER_VERSION}+ transcripts omit tool payloads:\n  ${violations.join("\n  ")}`,
  ).toEqual([]);
});

test("the runner declares the payload version and maps the fields onto it", () => {
  // The invariant above inspects nothing until the first v3 run lands, so
  // without this the whole contract could be deleted from the runner and
  // stay green forever.
  const src = fs.readFileSync(
    path.join(REPO_ROOT, "scripts", "run_test_prompts.py"),
    "utf-8",
  );

  const match = src.match(/^TRANSCRIPT_PARSER_VERSION\s*=\s*(\d+)/m);
  expect(
    match,
    "scripts/run_test_prompts.py must declare TRANSCRIPT_PARSER_VERSION",
  ).not.toBeNull();
  expect(
    Number(match![1]),
    `the payload guarantee is version ${PAYLOAD_PARSER_VERSION}; the runner must stamp at least that`,
  ).toBeGreaterThanOrEqual(PAYLOAD_PARSER_VERSION);

  // And it must actually read the fields off the metrics event rather than
  // stamping a version it does not honour.
  for (const field of [
    "input",
    "result_excerpt",
    "result_chars",
    "result_truncated",
    "result_citations",
  ]) {
    expect(
      new RegExp(`metric\\.get\\("${field}"`).test(src),
      `run_test_prompts.py must map ${field} from the tool_loop_metrics event`,
    ).toBe(true);
  }
});

test("the advisor's metric model still declares the payload fields", () => {
  // The producing half of the same contract. Asserted at the source
  // because a live turn only exercises the fields a *mock* dispatch
  // populates, and a silently-removed field would read as "this tool
  // returned nothing" rather than as a broken build.
  const src = fs.readFileSync(
    path.join(REPO_ROOT, "src", "advisor", "llm", "base.py"),
    "utf-8",
  );
  const model = src.slice(src.indexOf("class ToolCallMetric"));
  const body = model.slice(0, model.indexOf("\nclass "));

  for (const field of [
    "input",
    "result_excerpt",
    "result_chars",
    "result_truncated",
    "result_citations",
  ]) {
    expect(
      new RegExp(`^\\s{4}${field}\\s*:`, "m").test(body),
      `ToolCallMetric must declare ${field} — eval RCA depends on it`,
    ).toBe(true);
  }
});
