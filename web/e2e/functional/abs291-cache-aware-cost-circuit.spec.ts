// Functional: ABS-291 — cache-aware cost-circuit estimator.
//
// What this change does:
//   `estimate_request_input_tokens` in src/advisor/llm/budget.py now
//   returns a *billed-equivalent* token count rather than a raw count.
//   Tokens inside cached prefix regions (cache_system, cache_tools,
//   the rolling intra-loop breakpoint from ABS-285) are weighted at
//   Anthropic's cache-read rate (~10%) instead of full price.
//
// Why this matters:
//   Before ABS-291, the cost-circuit breaker fired when raw token
//   estimates exceeded the budget — even when most of those tokens
//   were cache reads billed at 10%. The breaker would force premature
//   synthesis on deep-question turns, degrading answers while saving
//   almost nothing (the cached prefix was cheap regardless).
//
// Why the assertion here is a regression guard:
//   The e2e stack drives the advisor through MockGateway, which
//   ignores cache_control flags (real cache reads only happen against
//   the live Anthropic backend). The discount arithmetic is therefore
//   verified in the Python unit tests (tests/advisor/llm/test_budget.py).
//   What IS observable end-to-end is that the refactored estimator
//   path runs correctly — the tool loop completes and terminates with
//   "end_turn", not "cost_circuit_trip". If the refactoring introduced
//   a bug (e.g. always returning 0, or always discounting everything),
//   the unit tests would catch it; this spec verifies the code path
//   runs end-to-end without crashing the chat route.
//
//   Calls FastAPI directly (not the Next.js proxy) — same reason as
//   abs9-credit-adoption.spec.ts: the proxy pins X-Test-User-Id.

import { execSync } from "node:child_process";
import * as path from "node:path";

import { expect, test } from "@playwright/test";

import { E2E_API_URL } from "../fixtures/test-env";

const TEST_USER_ID = `abs291-${Date.now()}-${Math.random()
  .toString(36)
  .slice(2, 8)}`;

test.beforeAll(() => {
  const repoRoot = path.resolve(__dirname, "..", "..", "..");
  const seed = path.join(repoRoot, "scripts", "seed_e2e_user.py");
  const venvPython = path.join(repoRoot, ".venv", "bin", "python");
  const pgPort = process.env.PG_PORT || "5433";
  const databaseUrl =
    process.env.DATABASE_URL ||
    `postgresql+psycopg://layer1:layer1@localhost:${pgPort}/layer1_test`;
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

async function openCase(
  request: import("@playwright/test").APIRequestContext,
): Promise<number> {
  const res = await request.post(`${E2E_API_URL}/v1/cases`, {
    headers: { "X-Test-User-Id": TEST_USER_ID },
    data: {
      anchor_label: "100 Robie Street, Halifax (ABS-291)",
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

async function chatSse(
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
    timeout: 20_000,
  });
  const body = await res.text();
  expect(
    res.status(),
    `chat failed: ${res.status()} ${body.slice(0, 400)}`,
  ).toBe(200);
  return body;
}

/**
 * Decode the `tool_loop_metrics` event from an SSE response body.
 * Returns null if the event is absent (e.g. the stream errored before
 * the metrics were emitted).
 */
function decodeToolLoopMetrics(
  body: string,
): { terminated_reason: string; iterations: number } | null {
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
    if (parsed.type === "tool_loop_metrics") {
      return {
        terminated_reason: String(parsed.terminated_reason ?? ""),
        iterations: Number(parsed.iterations ?? 0),
      };
    }
  }
  return null;
}

function isRecord(v: unknown): v is Record<string, unknown> {
  return typeof v === "object" && v !== null;
}

// ---------------------------------------------------------------------------
// Main guard: the refactored estimator must not prematurely trip the breaker
// ---------------------------------------------------------------------------

test("tool loop terminates with end_turn (not cost_circuit_trip) after cache-aware estimator", async ({
  request,
}) => {
  const caseId = await openCase(request);
  const body = await chatSse(
    request,
    caseId,
    "What is the minimum front yard setback?",
  );

  const metrics = decodeToolLoopMetrics(body);
  expect(
    metrics,
    "tool_loop_metrics event must be present in the SSE stream",
  ).not.toBeNull();
  if (!metrics) return; // unreachable — satisfies type-checker

  expect(
    metrics.terminated_reason,
    `expected end_turn but got ${metrics.terminated_reason} — ` +
      "the cache-aware estimator may have miscounted, causing a premature cost_circuit_trip",
  ).toBe("end_turn");

  // Sanity check: at least one iteration ran (search → answer round).
  expect(metrics.iterations).toBeGreaterThanOrEqual(1);
});
