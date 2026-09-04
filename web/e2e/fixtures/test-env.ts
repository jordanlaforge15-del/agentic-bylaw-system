// Shared environment + helpers for the Playwright suite.
//
// One concept: the test backend ("advisor.api.e2e_server") accepts an
// ``X-Test-User-Id`` header for auth. The Next.js proxy in
// ``web/lib/advisor-auth.ts`` forwards that header automatically when
// no real Clerk secret is configured, so individual specs don't need
// to manage auth headers — they just navigate. The fixtures below
// surface a few resets (case state, page) that specs share.

import { test as base, expect, type Page } from "@playwright/test";

// Stable id sent in every chat request. Matches the seed user in
// scripts/seed_e2e_user.py.
export const DEMO_USER_ID =
  process.env.E2E_USER_ID || "demo-user-1";

// Web port served by `scripts/e2e-up.sh`. Tests use baseURL from
// the Playwright config; this is exported for tests that need to
// build raw URLs (e.g. external nav after sign-in).
export const E2E_BASE_URL =
  process.env.E2E_BASE_URL || "http://localhost:3001";

// FastAPI test server. Tests use this when they need to short-circuit
// the Next.js proxy (e.g. open a case in API setup before navigating).
export const E2E_API_URL =
  process.env.E2E_API_URL || "http://127.0.0.1:8001";

/**
 * Open a case via the API so a test starts with a known case_id in
 * the URL. Faster and more reliable than driving the case-open form
 * — that form is itself one of the things we test, so most other
 * specs should bypass it.
 */
export async function openCaseViaApi(opts: {
  anchorLabel?: string;
  anchorKind?: "address" | "project_ref" | "development_application";
  tier?: "quick" | "standard" | "complex";
} = {}): Promise<{ caseId: number }> {
  const {
    anchorLabel = `e2e-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`,
    anchorKind = "address",
    tier = "standard",
  } = opts;
  const res = await fetch(`${E2E_API_URL}/v1/cases`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      "X-Test-User-Id": DEMO_USER_ID,
    },
    body: JSON.stringify({
      anchor_label: anchorLabel,
      anchor_kind: anchorKind,
      tier,
    }),
  });
  if (!res.ok) {
    throw new Error(
      `openCaseViaApi failed: ${res.status} ${await res.text()}`,
    );
  }
  const data = (await res.json()) as { case: { id: number } };
  return { caseId: data.case.id };
}

/**
 * Wait until React has hydrated the element matching ``selector``.
 *
 * ``page.goto`` resolves on the load event, which on a Next dev build is well
 * before the client bundle has attached handlers. Filling a *controlled* input
 * in that window is silently lost: ``fill`` writes the DOM value, no React
 * onChange fires, and hydration then resets the node to the server-rendered
 * empty string. The form's state stays empty, so a CTA gated on "all fields
 * present" never enables and the click times out 30s later — the flake
 * signature the ticket recorded for smoke/03-open-case (ABS-460).
 *
 * Probing for a ``__reactProps*`` key is how the shell's own specs already
 * detect this (abs218), lifted here so every form-driving spec can share it.
 */
export async function waitForHydration(
  page: Page,
  selector: string,
): Promise<void> {
  await page.waitForFunction((sel) => {
    const el = document.querySelector(sel);
    if (!el) return false;
    return Object.keys(el).some((k) => k.startsWith("__reactProps"));
  }, selector);
}

/**
 * Fill the /cases/new open-case form and start the conversation.
 *
 * Waits for hydration before typing and for the CTA to actually enable before
 * clicking, so a slow dev-server hydration surfaces as "the CTA never enabled"
 * rather than an unattributable 30s click timeout.
 */
export async function startConversationViaForm(
  page: Page,
  opts: { anchor: string; question: string },
): Promise<void> {
  await waitForHydration(page, 'input[placeholder^="e.g. 1234 Main St"]');
  await page.getByPlaceholder(/1234 Main St, Halifax/).fill(opts.anchor);
  await page.getByPlaceholder(/Ask your question/).fill(opts.question);
  const cta = page.getByTestId("start-conversation-btn");
  await expect(cta).toBeEnabled();
  await cta.click();
}

/**
 * Wait until GET /api/chat/sessions lists a session for ``anchorFragment``.
 *
 * The sidebar is not a live view: it refetches only when the shell bumps its
 * refresh trigger, which for a chat turn happens once, in ``send``'s finally.
 * A spec that starts a turn and immediately switches conversations therefore
 * gets exactly one refetch, and it races the backend's commit of the new
 * session row. Lose that race and the conversation is simply absent from the
 * sidebar with nothing scheduled to fetch it again — which is how
 * abs218 failed intermittently (ABS-460).
 *
 * Waiting on the API first makes the subsequent refetch deterministic. It
 * does not wait for the answer: the session row is committed when the turn
 * opens, long before the stream finishes, so a mid-stream spec stays
 * mid-stream.
 */
export async function waitForSessionListed(
  page: Page,
  anchorFragment: string,
): Promise<void> {
  await expect
    .poll(
      async () => {
        const res = await page.request.get("/api/chat/sessions");
        if (!res.ok()) return false;
        const data = (await res.json()) as {
          sessions?: Array<{ anchor_label?: string | null }>;
        };
        return (data.sessions ?? []).some((s) =>
          (s.anchor_label ?? "").includes(anchorFragment),
        );
      },
      {
        timeout: 10_000,
        message: `no session listed for anchor containing "${anchorFragment}"`,
      },
    )
    .toBe(true);
}

/**
 * Wait for a sidebar case switch to land in the URL.
 *
 * ``selectSession`` in the chat shell fetches the target session's messages
 * AND its feedback map before it rewrites ``?case_id=``, so the URL flip is
 * gated on two API round trips rather than on the click. Idle that is ~150ms,
 * which is why the specs that drive this used to hand-roll a 5s wait.
 *
 * Under the suite's four parallel workers those same two requests have been
 * measured at 4.5s against the single-process e2e FastAPI (ABS-460: an
 * abs421 run where the click registered at t+13.6s and the URL flipped at
 * t+18.3s, 0.3s past a 5s deadline). The switch was working; the wait was
 * just tuned to an unloaded stack. 15s keeps a real "the click did nothing"
 * regression failing well inside the 30s per-test cap while leaving three
 * times the measured worst case as headroom.
 */
export async function expectCaseIdInUrl(
  page: Page,
  caseId: number,
): Promise<void> {
  await expect(page).toHaveURL(new RegExp(`case_id=${caseId}`), {
    timeout: 15_000,
  });
}

/**
 * Wait for the chat thread to render an assistant message containing
 * the expected substring. Polls until the timeout — the SSE stream
 * appends text chunks incrementally so a naive equality check would
 * race.
 */
export async function waitForAssistantText(
  page: Page,
  expected: string | RegExp,
  opts: { timeout?: number } = {},
): Promise<void> {
  const re = expected instanceof RegExp ? expected : new RegExp(expected);
  await expect(page.locator("[data-testid='chat-thread']"))
    .toContainText(re, { timeout: opts.timeout ?? 10_000 });
}

/**
 * Test fixture that auto-mints auth cookies on every browser context
 * before the first navigation. The stack runs Clerk-mock mode
 * (E2E_CLERK_MOCK=1 + a test CLERK_SECRET_KEY, see scripts/e2e-up.sh),
 * so proxy.ts always takes the clerkMiddleware branch: the
 * abs_test_sub_user_id cookie is what makes a request "signed in", and
 * abs_test_clerk_jwt is forwarded to FastAPI as a Bearer token.
 *
 * ABS-530 removed the shared-password gate this fixture used to mint
 * an abs_demo cookie against. The cookies below are now the ONLY thing
 * standing between a spec and a /sign-in redirect, so a failure to mint
 * them is fatal rather than best-effort.
 */
async function postWithRetry(
  context: any,
  url: string,
  data: any,
  maxAttempts: number = 3,
): Promise<any> {
  let lastError: Error | null = null;
  for (let attempt = 0; attempt < maxAttempts; attempt++) {
    try {
      const res = await context.request.post(url, { data });
      if (res.ok()) {
        return res;
      }
      const text = await res.text();
      lastError = new Error(
        `HTTP ${res.status()} ${text}`,
      );
      // Only retry on 5xx or transient errors; don't retry 4xx
      if (res.status() < 500) {
        throw lastError;
      }
    } catch (err) {
      lastError = err instanceof Error ? err : new Error(String(err));
      if (attempt < maxAttempts - 1) {
        // Wait before retrying: 100ms * (attempt + 1)
        await new Promise((resolve) =>
          setTimeout(resolve, 100 * (attempt + 1)),
        );
      }
    }
  }
  throw lastError || new Error("POST request failed");
}

export const test = base.extend<{ authedContext: void }>({
  authedContext: [
    async ({ context, baseURL }, use) => {
      const target = baseURL ?? E2E_BASE_URL;
      // Mint a test JWT for the demo user. This is the whole of the
      // auth setup: the sub cookie satisfies clerkMiddleware in
      // proxy.ts and the JWT cookie is forwarded to FastAPI as a
      // Bearer token, so the Clerk path is exercised end-to-end.
      const jwtRes = await postWithRetry(
        context,
        `${E2E_API_URL}/v1/_test/mint-jwt`,
        {
          sub: DEMO_USER_ID,
          email: `${DEMO_USER_ID}@e2e.test`,
        },
      );
      const { token } = (await jwtRes.json()) as { token: string };
      const url = new URL(target);
      await context.addCookies([
        {
          name: "abs_test_sub_user_id",
          value: DEMO_USER_ID,
          domain: url.hostname,
          path: "/",
          httpOnly: false,
          secure: false,
          sameSite: "Lax",
        },
        {
          name: "abs_test_clerk_jwt",
          value: token,
          domain: url.hostname,
          path: "/",
          httpOnly: false,
          secure: false,
          sameSite: "Lax",
        },
      ]);
      await use();
    },
    { auto: true },
  ],
});

export { expect };
