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
 * Demo password matching DEMO_PASSWORD on the Next.js dev process
 * (see scripts/e2e-up.sh). Tests need this to mint the abs_demo
 * cookie so `/app` and `/admin` are reachable behind proxy.ts's
 * password-gate fallback.
 */
export const E2E_DEMO_PASSWORD =
  process.env.E2E_DEMO_PASSWORD || "e2e-demo-pw";

/**
 * Test fixture that auto-mints an `abs_demo` cookie on every browser
 * context before the first navigation. The legacy shared-password
 * gate in `web/proxy.ts` redirects /app and /admin to /access without
 * this cookie; the e2e suite sets `DEMO_PASSWORD` on the dev server
 * so the gate has a known value to authenticate against.
 */
export const test = base.extend<{ authedContext: void }>({
  authedContext: [
    async ({ context, baseURL }, use) => {
      const target = baseURL ?? E2E_BASE_URL;
      const res = await context.request.post(`${target}/api/access`, {
        data: { gate: "demo", password: E2E_DEMO_PASSWORD },
      });
      if (!res.ok()) {
        throw new Error(
          `failed to mint abs_demo cookie: HTTP ${res.status()} ${await res.text()}`,
        );
      }
      await use();
    },
    { auto: true },
  ],
});

export { expect };
