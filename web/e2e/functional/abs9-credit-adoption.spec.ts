// Functional regression: ABS-9.
//
// Before the fix, a user who spent their last available credit
// opening a case got a 402 on their first /v1/chat turn — the chat
// route claimed a second credit instead of adopting the one
// open_case had already reserved against the case. The fix in
// src/advisor/api/quota.py teaches reserve_credit_for_session to
// adopt the case-pre-reserved credit (session_id IS NULL) before
// falling back to _claim_available_credit. See pytest:
//   tests/advisor/api/test_quota.py
//     ::test_first_chat_after_open_case_adopts_the_case_reserved_credit
//
// This spec adds an end-to-end check at the HTTP boundary of the
// running FastAPI test server, where the pytest tests use an
// in-process session. It uses a dedicated user (unique per run) so
// the 1-credit setup can't race the rest of the suite, which leans
// on the shared seeded demo user having 200 credits per tier. The
// spec calls FastAPI directly because the Next.js proxy ties the
// upstream X-Test-User-Id header to ADVISOR_DEMO_USER_ID at process
// start — there's no per-request override. Browser-level SSE
// rendering is already covered by smoke/04-chat-sse.spec.ts; this
// spec's job is to keep the credit-handoff regression from coming
// back at the deployed-process boundary.

import { execSync } from "node:child_process";
import * as path from "node:path";

import { expect, test } from "@playwright/test";

import { E2E_API_URL } from "../fixtures/test-env";

// Brand-new user id per run: seed_e2e_user.py is idempotent in the
// top-up direction only (it never burns down), so for a fresh id
// "--credits-per-tier 1" yields exactly one available credit per
// tier — exactly the state that triggered the original 402.
const TEST_USER_ID = `abs9-${Date.now()}-${Math.random()
  .toString(36)
  .slice(2, 8)}`;

test.beforeAll(() => {
  const repoRoot = path.resolve(__dirname, "..", "..", "..");
  const seed = path.join(repoRoot, "scripts", "seed_e2e_user.py");
  const venvPython = path.join(repoRoot, ".venv", "bin", "python");
  // Honor PG_PORT so this spec seeds against the correct Postgres
  // container when a worktree overrides it for parallel `make e2e`
  // (see docs/E2E_TESTING.md#parallel-worktrees). Without this, the
  // seed lands in the default :5432 instance while FastAPI on the
  // worktree's overridden port queries a different DB — the user
  // never appears and open_case returns 500.
  const pgPort = process.env.PG_PORT || "5432";
  const databaseUrl =
    process.env.DATABASE_URL ||
    `postgresql+psycopg://layer1:layer1@localhost:${pgPort}/layer1_test`;

  execSync(
    `"${venvPython}" "${seed}" --user-id "${TEST_USER_ID}" ` +
      `--email "${TEST_USER_ID}@e2e.test" --credits-per-tier 1`,
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

test("open_case + first chat must not 402 when only one credit remains", async ({
  request,
}) => {
  // 1. open_case at standard tier consumes the one available
  //    standard credit and leaves it reserved against the case
  //    with session_id IS NULL. Anything other than 200 here means
  //    the seed step didn't land — the test can't expose the bug.
  const openRes = await request.post(`${E2E_API_URL}/v1/cases`, {
    headers: { "X-Test-User-Id": TEST_USER_ID },
    data: {
      anchor_label: "1991b prince arthur (ABS-9)",
      anchor_kind: "address",
      tier: "standard",
    },
  });
  expect(
    openRes.status(),
    `open_case failed: ${openRes.status()} ${await openRes.text()}`,
  ).toBe(200);
  const openBody = (await openRes.json()) as { case: { id: number } };
  const caseId = openBody.case.id;

  // 2. First chat turn for the brand-new session. Pre-fix this
  //    would 402 with no_available_credit because the chat route
  //    looked for an existing reservation by session_id only, found
  //    none on a fresh session, and tried to claim a second credit
  //    (of which there are none). Post-fix it adopts the case-
  //    reserved credit minted above.
  const chatRes = await request.post(`${E2E_API_URL}/v1/chat`, {
    headers: {
      "X-Test-User-Id": TEST_USER_ID,
      "Content-Type": "application/json",
      Accept: "text/event-stream",
    },
    data: {
      message: "What is the minimum front yard setback?",
      case_id: caseId,
      session_id: null,
    },
    // SSE: the request resolves only when the stream closes. The
    // MockGateway dispatcher returns in well under a second, but
    // leave headroom for CI cold starts.
    timeout: 15_000,
  });

  // The regression signal: HTTP 402 means the chat route tried to
  // claim a second credit. Surfacing the body here makes the
  // failure self-diagnosing — pre-fix it would contain
  // {"code":"no_available_credit","tier":"standard",...}.
  const chatBody = await chatRes.text();
  expect(
    chatRes.status(),
    `chat failed: ${chatRes.status()} ${chatBody.slice(0, 400)}`,
  ).toBe(200);

  // Sanity-check that real content streamed back — guards against
  // a future code path that bypasses the credit error but produces
  // an empty stream and still 200s. The mock dispatcher's final
  // text ends with the canonical RC-LUB §15.4 citation that
  // 04-chat-sse.spec.ts also pins on.
  //
  // The SSE wire format wraps each event payload in JSON, so any
  // non-ASCII characters (including §, U+00A7) are emitted as
  // ``§`` escape sequences. Matching the literal § against the
  // raw response body therefore fails even when the citation is
  // present and correct. Decode the JSON ``data:`` payloads first
  // and assert against the concatenated assistant text — that's
  // what the user actually sees once the browser parses the stream.
  expect(chatBody).toMatch(/text_delta/);
  expect(decodeAssistantText(chatBody)).toMatch(/RC-LUB §15\.4/);
});


// SSE event-stream → assistant text. Each event in the stream is an
// ``event:`` line followed by one or more ``data:`` JSON payloads.
// We accumulate from ``content_block_delta.text_delta`` because that's
// the stream shape the real Anthropic gateway emits — staying close
// to the production wire format keeps this helper useful if the mock
// ever stops emitting the convenience ``content_block_start.text``.
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
      // Non-JSON data lines (e.g. session boot frames) are ignored.
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
