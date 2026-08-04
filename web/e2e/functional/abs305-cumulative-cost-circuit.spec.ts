// Functional: ABS-305 — cumulative per-turn cost breaker.
//
// What this change does:
//   The per-request cost-circuit breaker (ABS-291) caps a SINGLE gateway
//   call at ~150k billed-equivalent input tokens. It does not bound a
//   turn's cumulative spend: a deep tool loop of many sub-cap requests
//   can still bill $10+ in aggregate (the documented 2026-05-11 runaway
//   turns of $12.93 / $9.30). ABS-305 adds a second breaker in
//   run_tool_loop that sums each iteration's billed-equivalent estimate
//   and forces synthesis with terminated_reason="cumulative_cost_trip"
//   once the running total would cross the cumulative ceiling
//   (ADVISOR_TURN_CUMULATIVE_TOKEN_BUDGET, default ~165k ≈ $2.50). This
//   is the load-bearing cost primitive that bounds each PAID answer in
//   the priced-question catalog.
//
// Why this is observable end-to-end:
//   The MOCK_CUMULATIVE_TRIP keyword drives the mock dispatcher to emit a
//   large filler preamble on every search round, so the conversation —
//   and thus each request's estimate — climbs round over round while no
//   single request trips the per-request cap. After a handful of rounds
//   the cumulative breaker fires. We open a Complex-tier case
//   (max_iterations=55) so the iteration cap can't fire first, then
//   assert the tool_loop_metrics SSE event reports
//   terminated_reason="cumulative_cost_trip". A regression that drops or
//   misorders the cumulative check would surface as "end_turn" (breaker
//   never fired) or "iteration_cap" / "cost_circuit_trip" (wrong path).
//
//   Calls FastAPI directly (not the Next.js proxy) — same reason as
//   abs291-cache-aware-cost-circuit.spec.ts: the proxy pins X-Test-User-Id.

import { execSync } from "node:child_process";
import * as path from "node:path";

import { expect, test } from "@playwright/test";

import { E2E_API_URL } from "../fixtures/test-env";

const TEST_USER_ID = `abs305-${Date.now()}-${Math.random()
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

async function openComplexCase(
  request: import("@playwright/test").APIRequestContext,
): Promise<number> {
  const res = await request.post(`${E2E_API_URL}/v1/cases`, {
    headers: { "X-Test-User-Id": TEST_USER_ID },
    data: {
      anchor_label: "200 Barrington Street, Halifax (ABS-305)",
      anchor_kind: "address",
      tier: "complex",
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
    timeout: 40_000,
  });
  const body = await res.text();
  expect(
    res.status(),
    `chat failed: ${res.status()} ${body.slice(0, 400)}`,
  ).toBe(200);
  return body;
}

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

test("a runaway deep turn trips the cumulative cost breaker (not the iteration cap)", async ({
  request,
}) => {
  const caseId = await openComplexCase(request);
  const body = await chatSse(
    request,
    caseId,
    "MOCK_CUMULATIVE_TRIP deep multi-round runaway turn",
  );

  const metrics = decodeToolLoopMetrics(body);
  expect(
    metrics,
    "tool_loop_metrics event must be present in the SSE stream",
  ).not.toBeNull();
  if (!metrics) return; // unreachable — satisfies type-checker

  expect(
    metrics.terminated_reason,
    `expected cumulative_cost_trip but got ${metrics.terminated_reason} — ` +
      "the cumulative per-turn breaker did not fire (end_turn = never " +
      "tripped; iteration_cap = wrong breaker; cost_circuit_trip = " +
      "per-request breaker fired instead of the cumulative one)",
  ).toBe("cumulative_cost_trip");

  // The breaker fires AFTER at least one round accumulated, and well
  // before the Complex-tier iteration cap (55).
  expect(metrics.iterations).toBeGreaterThanOrEqual(1);
  expect(metrics.iterations).toBeLessThan(55);
});
