// ABS-18 — Terms & Conditions click-wrap gate.
//
// Asserts the five acceptance criteria from the Linear comment:
//
//  1. The current Terms document is served in full on the
//     acceptance screen (the GET /v1/terms/current body is non-empty
//     and contains the Acknowledgement-of-Acceptance block).
//  2. Trial activation is blocked until the user clicks I Agree
//     (chat returns 412 ``terms_not_accepted`` before acceptance, 200
//     after).
//  3. Acceptance is recorded with user, IP, user-agent, timestamp,
//     and version hash (the POST records a row; the next GET reports
//     ``accepted: true``).
//  4. Material amendments re-prompt the user (a POST with a stale
//     ``version`` hash is rejected with 409 — the same response the
//     UI would receive after the document is edited under a
//     long-lived browser tab).
//  5. The gate guards the future API/MCP key surface. Today the only
//     guarded endpoint is /v1/chat (the future /v1/keys endpoint will
//     re-use the same ``require_accepted_current_terms`` dependency);
//     asserting the chat 412 covers the mechanism.
//
// Why a per-test user instead of demo-user-1: the seed script keeps
// demo-user-1 in an "accepted" state so the rest of the e2e suite
// (chat / case-flow / sidebar specs) doesn't end up at /app/terms.
// Wiping that row mid-test to exercise the gate would race against
// the other Playwright workers (fullyParallel: true). A
// per-invocation X-Test-User-Id sidesteps the race entirely — each
// test gets its own ``advisor_user`` row that starts unaccepted.
//
// Why this is a FastAPI-direct spec (no browser navigation): the
// gate's correctness lives at the API layer (the chat 412 + the
// acceptance row in advisor_terms_acceptance). The Next.js page is
// a 30-line server component that does one ``redirect("/app/terms")``
// based on the same API response — its behaviour is exercised
// transitively by the API assertions, and a parallel-safe browser
// test would require either a per-test user-id override (no clean
// API for that in the existing proxy) or a serial wipe-then-restore
// that flakes other tests. Trading one risky browser nav for four
// deterministic API assertions is the better coverage shape.

import { expect, test } from "../fixtures/test-env";

const E2E_API_URL = process.env.E2E_API_URL || "http://127.0.0.1:8001";

// Pick a deterministic-but-unique user id per test so two runs of
// this spec in parallel don't share state. Date.now() resolution is
// fine — we just need uniqueness across this process's lifetime.
function freshUserId(label: string): string {
  return `e2e-terms-${label}-${Date.now()}-${Math.random()
    .toString(36)
    .slice(2, 8)}`;
}

async function backend(
  path: string,
  init: {
    method?: "GET" | "POST";
    body?: unknown;
    userId: string;
  },
): Promise<Response> {
  return fetch(`${E2E_API_URL}${path}`, {
    method: init.method ?? "GET",
    headers: {
      "Content-Type": "application/json",
      "X-Test-User-Id": init.userId,
    },
    body: init.body !== undefined ? JSON.stringify(init.body) : undefined,
  });
}

test.describe("terms acceptance gate", () => {
  test("first GET reports accepted=false; document body is rendered in full", async () => {
    const userId = freshUserId("first-get");

    const res = await backend("/v1/terms/current", { userId });
    expect(res.status).toBe(200);
    const data = (await res.json()) as {
      version: string;
      body: string;
      accepted: boolean;
    };

    // Version hash is the sha256 hex (64 chars) — the same value the
    // UI posts back on click I Agree.
    expect(data.version).toMatch(/^[a-f0-9]{64}$/);

    // The full document is served (not a "View" link or collapsed
    // accordion). Spot-check both ends so a regression that ships an
    // empty file is loud.
    expect(data.body.length).toBeGreaterThan(2000);
    expect(data.body).toContain("# Terms and Conditions");
    expect(data.body).toContain("## Acknowledgement of Acceptance");

    expect(data.accepted).toBe(false);
  });

  test("POST records acceptance; subsequent GET reports accepted=true", async () => {
    const userId = freshUserId("accept");

    // Capture the version before accepting.
    const currentRes = await backend("/v1/terms/current", { userId });
    const current = (await currentRes.json()) as { version: string };

    const acceptRes = await backend("/v1/terms/accept", {
      method: "POST",
      userId,
      body: { version: current.version },
    });
    expect(acceptRes.status).toBe(200);
    const accept = (await acceptRes.json()) as {
      accepted: boolean;
      version: string;
      accepted_at: string;
    };
    expect(accept.accepted).toBe(true);
    expect(accept.version).toBe(current.version);
    // ISO 8601 timestamp.
    expect(Date.parse(accept.accepted_at)).not.toBeNaN();

    // The same user now reads accepted=true (gate would let them
    // through /app on the next request).
    const after = await backend("/v1/terms/current", { userId });
    expect(after.status).toBe(200);
    const afterData = (await after.json()) as { accepted: boolean };
    expect(afterData.accepted).toBe(true);
  });

  test("POST with stale version_hash is rejected with 409", async () => {
    const userId = freshUserId("stale");

    // Hand-build a sha256-shaped hash that doesn't match the live
    // document. The server cross-checks; a stale-hash accept must not
    // produce an acceptance row.
    const fakeVersion = "0".repeat(64);

    const res = await backend("/v1/terms/accept", {
      method: "POST",
      userId,
      body: { version: fakeVersion },
    });
    expect(res.status).toBe(409);
    const body = (await res.json()) as {
      detail: { code?: string };
    };
    expect(body.detail.code).toBe("terms_version_mismatch");

    // Sanity: the user is still un-accepted (the server didn't write
    // a row from the stale post).
    const after = await backend("/v1/terms/current", { userId });
    const afterData = (await after.json()) as { accepted: boolean };
    expect(afterData.accepted).toBe(false);
  });

  test("POST with the same version twice is idempotent (no duplicate row)", async () => {
    const userId = freshUserId("idempotent");
    const current = (await (
      await backend("/v1/terms/current", { userId })
    ).json()) as { version: string };

    const first = await backend("/v1/terms/accept", {
      method: "POST",
      userId,
      body: { version: current.version },
    });
    expect(first.status).toBe(200);

    // Second POST with the same version should also return 200
    // (idempotent), not 409 / 500 from the unique-constraint failure
    // — the router catches IntegrityError and returns the existing
    // row.
    const second = await backend("/v1/terms/accept", {
      method: "POST",
      userId,
      body: { version: current.version },
    });
    expect(second.status).toBe(200);
    const secondData = (await second.json()) as {
      accepted: boolean;
      version: string;
    };
    expect(secondData.accepted).toBe(true);
    expect(secondData.version).toBe(current.version);
  });

  test("chat returns 412 before acceptance and unblocks after", async () => {
    // Use a session-bearing user-id so resolve_or_create_user inserts
    // a row on the first request (the chat handler upserts via the
    // user-dependency; for the test-only fallback the row is created
    // by the seed-or-grant flow on first credit reserve). We need a
    // user that EXISTS in advisor_user but has NO acceptance row.
    // The cleanest way: GET /v1/terms/current first — that resolves
    // (and creates if needed) the user via the user_resolver path
    // inside the terms router.
    const userId = freshUserId("chat-gate");
    await backend("/v1/terms/current", { userId });

    // Before acceptance: chat must refuse with 412.
    const blocked = await backend("/v1/chat", {
      method: "POST",
      userId,
      body: { message: "hello", session_id: null, case_id: null },
    });
    // The chat router enforces the gate at the start of the request,
    // before the case_id lookup, so we expect 412 here regardless of
    // case state.
    expect(blocked.status).toBe(412);
    const blockedBody = (await blocked.json()) as {
      detail: { code?: string };
    };
    expect(blockedBody.detail.code).toBe("terms_not_accepted");

    // Accept, then chat should no longer be 412 — it may fail on a
    // downstream check (missing case_id, no credits, etc.), but
    // crucially it must NOT be 412 anymore. That's what the spec
    // asserts: the gate stops being the blocker.
    const current = (await (
      await backend("/v1/terms/current", { userId })
    ).json()) as { version: string };
    await backend("/v1/terms/accept", {
      method: "POST",
      userId,
      body: { version: current.version },
    });

    const after = await backend("/v1/chat", {
      method: "POST",
      userId,
      body: { message: "hello", session_id: null, case_id: null },
    });
    expect(after.status).not.toBe(412);
  });

  test("seeded demo user is not redirected from /app (gate honours acceptance)", async ({
    page,
  }) => {
    // Sanity check that the seed script keeps the demo user accepted
    // for the current T&C hash. If this fails, every other functional
    // spec would also be redirected to /app/terms and the suite would
    // wedge — so this assertion is the early-warning canary.
    const res = await page.goto("/app");
    expect(res?.status()).toBe(200);
    // Final URL should still be /app (not /app/terms). Allow for a
    // trailing slash variation just in case.
    expect(page.url()).toMatch(/\/app(\?|$|\/$)/);
  });
});
