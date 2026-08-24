// Functional regression: ABS-519.
//
// Grading evals/runs/zone-typology-all8 against the golden subset turned up a
// CORRECT refusal (TC-026, 6051 Oakland Road, ER-2, "can I build four
// townhouses?") carrying a heading that asserted the opposite of its own body:
//
//   ### 1. Townhouse Dwelling Use — Permitted in ER-2 (with conditions)
//
//   Table 1B confirms that townhouse dwelling use is permitted in the ER-3
//   zone, but not in ER-2.
//
// The prose is right and the summary line is right; the heading is the most
// scannable element on the page, and "(with conditions)" makes it read as a
// qualified yes. A homeowner deciding whether to call an architect acts on the
// heading.
//
// Two-layer fix, same shape as ABS-263's hedging:
//   1. docs/agent/persona.md tells the model headings must state the section's
//      conclusion (drives the live model).
//   2. src/advisor/chat/heading_consistency.py is the deterministic net: the
//      ChatSession pipeline rewrites a heading whose permission claim its own
//      section denies, before the stream is built.
//
// This spec exercises layer 2 end-to-end through the real ChatSession pipeline
// against the MockGateway. MOCK_CONTRADICTORY_HEADING makes the mock emit the
// contradicting heading verbatim; the repaired heading in the decoded SSE
// stream is therefore product code, not a mock string. The second test pins
// the no-op: an answer whose headings already agree is passed through
// untouched.
//
// Calls FastAPI directly (not the Next.js proxy) for the same reason as
// abs263-high-liability-hedging.spec.ts: the proxy pins X-Test-User-Id to
// ADVISOR_DEMO_USER_ID at process start, with no per-request override.

import { execSync } from "node:child_process";
import * as path from "node:path";

import { expect, test } from "@playwright/test";

import { E2E_API_URL } from "../fixtures/test-env";
import { resolveDatabaseUrl } from "../helpers/database-url";

const TEST_USER_ID = `abs519-${Date.now()}-${Math.random()
  .toString(36)
  .slice(2, 8)}`;

test.beforeAll(() => {
  const repoRoot = path.resolve(__dirname, "..", "..", "..");
  const seed = path.join(repoRoot, "scripts", "seed_e2e_user.py");
  const venvPython = path.join(repoRoot, ".venv", "bin", "python");
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
      anchor_label: "6051 Oakland Road, Halifax (ABS-519)",
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
    timeout: 15_000,
  });
  const body = await res.text();
  expect(
    res.status(),
    `chat failed: ${res.status()} ${body.slice(0, 400)}`,
  ).toBe(200);
  return body;
}

test("a refusal's heading is repaired to agree with its body", async ({
  request,
}) => {
  const caseId = await openCase(request);
  const body = await chat(
    request,
    caseId,
    "Can I build four townhouses at this ER-2 property? " +
      "MOCK_CONTRADICTORY_HEADING",
  );
  const text = decodeAssistantText(body);

  // The heading now carries the section's actual verdict...
  expect(text).toMatch(/#+ .*Townhouse Dwelling Use — Not Permitted in ER-2/);
  // ...and no longer reads as a qualified yes.
  expect(text).not.toMatch(/Permitted in ER-2 \(with conditions\)/);
  // The body — already correct — survived the rewrite unchanged.
  expect(text).toMatch(/permitted in the ER-3 zone/);
  expect(text).toMatch(/not in ER-2/);

  // Section 2 carries the other shape a denial takes in by-law prose: the
  // permission word qualifying a noun, "is not an allowed use in ER-2".
  // Un-negated that is a topic ("Permitted Uses in ER-2") and the guard must
  // leave it alone; negated it is a verdict, and the heading must follow.
  expect(text).toMatch(/#+ .*Four-Unit Dwelling — Not Allowed in ER-2/);
  expect(text).toMatch(/not an allowed use\** in ER-2/);
});

test("an answer whose headings already agree is passed through untouched", async ({
  request,
}) => {
  const caseId = await openCase(request);
  // The default mock answer carries no headings and no permission claim, so
  // the guard is a no-op: content streams back verbatim.
  const body = await chat(request, caseId, "What is the minimum front setback?");
  const text = decodeAssistantText(body);

  expect(text).toMatch(/Based on the bylaw evidence/);
  expect(text).not.toMatch(/Not Permitted/);
  expect(text).not.toMatch(/Permission in /);
});

// SSE event-stream → assistant text. Mirrors
// abs263-high-liability-hedging.spec.ts: accumulate
// ``content_block_delta.text_delta``, the wire shape the real gateway emits.
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
