// Functional regression: ABS-263.
//
// The ABS-260 production-readiness sweep (TC-005) caught the advisor
// handing a developer a full feasibility envelope — height, FAR, lot
// coverage, setbacks, parking — across six turns without once telling
// them to confirm with HRM / a planner or qualifying the numbers as
// general / non-legal information. That's a user-safety and liability
// problem: a developer could commit a building program to an answer
// that's actually precinct-specific.
//
// The fix is two-layer:
//   1. docs/agent/persona.md instructs the model to close feasibility
//      answers with a verify-with-a-planner note (drives the live
//      model; covered by the TC-005 re-run + tests/advisor/chat).
//   2. src/advisor/chat/hedging.py is a deterministic safety net: the
//      ChatSession pipeline appends a templated qualifier to any
//      feasibility-grade answer that forgot to hedge, and is a no-op
//      for narrow lookups and already-hedged answers.
//
// This spec exercises layer 2 end-to-end through the real ChatSession
// pipeline against the MockGateway. The mock never writes a hedge; the
// hedge in the decoded SSE stream is therefore product code, not a
// hard-coded mock string. The second test pins the carve-out: a simple
// single-dimension lookup must stay lean (no hedge dance), the explicit
// ABS-263 acceptance criterion.
//
// Calls FastAPI directly (not the Next.js proxy) for the same reason as
// abs9-credit-adoption.spec.ts: the proxy pins X-Test-User-Id to
// ADVISOR_DEMO_USER_ID at process start, with no per-request override.

import { execSync } from "node:child_process";
import * as path from "node:path";

import { expect, test } from "@playwright/test";

import { E2E_API_URL } from "../fixtures/test-env";
import { resolveDatabaseUrl } from "../helpers/database-url";

// Dedicated user per run so the credit setup can't race the shared
// seeded demo user. Two tests, each opening one standard-tier case, so
// seed a comfortable margin of standard credits.
const TEST_USER_ID = `abs263-${Date.now()}-${Math.random()
  .toString(36)
  .slice(2, 8)}`;

test.beforeAll(() => {
  const repoRoot = path.resolve(__dirname, "..", "..", "..");
  const seed = path.join(repoRoot, "scripts", "seed_e2e_user.py");
  const venvPython = path.join(repoRoot, ".venv", "bin", "python");
  // Honor PG_PORT so the seed lands in the same Postgres the worktree's
  // FastAPI queries (see docs/E2E_TESTING.md#parallel-worktrees).
  const databaseUrl = resolveDatabaseUrl();

  execSync(
    `"${venvPython}" "${seed}" --user-id "${TEST_USER_ID}" ` +
      `--email "${TEST_USER_ID}@e2e.test" --credits-per-tier 5`,
    {
      env: {
        ...process.env,
        DATABASE_URL: databaseUrl,
        PYTHONPATH: `${path.join(repoRoot, "src")}:${
          process.env.PYTHONPATH || ""
        }`,
      },
      stdio: "inherit",
    },
  );
});

async function openCase(request: import("@playwright/test").APIRequestContext) {
  const openRes = await request.post(`${E2E_API_URL}/v1/cases`, {
    headers: { "X-Test-User-Id": TEST_USER_ID },
    data: {
      anchor_label: "100 Robie Street, Halifax (ABS-263)",
      anchor_kind: "address",
      tier: "standard",
    },
  });
  expect(
    openRes.status(),
    `open_case failed: ${openRes.status()} ${await openRes.text()}`,
  ).toBe(200);
  const body = (await openRes.json()) as { case: { id: number } };
  return body.case.id;
}

async function chat(
  request: import("@playwright/test").APIRequestContext,
  caseId: number,
  message: string,
): Promise<string> {
  const res = await request.post(`${E2E_API_URL}/v1/chat`, {
    headers: {
      "X-Test-User-Id": TEST_USER_ID,
      "Content-Type": "application/json",
      Accept: "text/event-stream",
    },
    data: { message, case_id: caseId, session_id: null },
    // SSE resolves only when the stream closes; mock returns fast but
    // leave headroom for CI cold starts.
    timeout: 15_000,
  });
  const body = await res.text();
  expect(
    res.status(),
    `chat failed: ${res.status()} ${body.slice(0, 400)}`,
  ).toBe(200);
  return body;
}

test("feasibility-grade answer is hedged toward HRM / a planner", async ({
  request,
}) => {
  const caseId = await openCase(request);
  // MOCK_FEASIBILITY makes the mock return a multi-dimension envelope
  // (height + FAR + coverage + setback + parking) with no hedging. The
  // ChatSession hedge injector should append the qualifier.
  const body = await chat(
    request,
    caseId,
    "We're scoping a residential tower on this HR-2 site — give me the " +
      "full feasibility envelope. MOCK_FEASIBILITY",
  );
  const text = decodeAssistantText(body);

  // The mock's feasibility numbers still came through (hedge is
  // appended, not a replacement).
  expect(text).toMatch(/Max FAR/);
  // ...and the injected hedge carries the ABS-260 verifier markers.
  expect(text).toMatch(/qualified planner/i);
  expect(text).toMatch(/HRM Planning/i);
  expect(text).toMatch(/not legal advice/i);
});

test("narrow single-dimension lookup stays lean (no hedge dance)", async ({
  request,
}) => {
  const caseId = await openCase(request);
  // The default mock final answer is a single-dimension setback line —
  // exactly the homeowner-style lookup that must NOT get the hedge.
  const body = await chat(
    request,
    caseId,
    "What is the minimum front yard setback?",
  );
  const text = decodeAssistantText(body);

  // Real content streamed back, but the verify-with-a-planner block is
  // absent — the carve-out the ABS-263 acceptance criteria require.
  expect(text.length).toBeGreaterThan(0);
  expect(text).not.toMatch(/qualified planner/i);
  expect(text).not.toMatch(/Before you act on these numbers/i);
});

// SSE event-stream → assistant text. Mirrors abs9-credit-adoption.spec.ts:
// accumulate ``content_block_delta.text_delta`` because that's the wire
// shape the real Anthropic gateway emits.
function decodeAssistantText(body: string): string {
  let text = "";
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
    if (!isRecord(parsed)) continue;
    if (parsed.type === "content_block_delta") {
      const delta = parsed.text_delta;
      if (typeof delta === "string") text += delta;
    }
  }
  return text;
}

function isRecord(v: unknown): v is Record<string, unknown> {
  return typeof v === "object" && v !== null;
}
