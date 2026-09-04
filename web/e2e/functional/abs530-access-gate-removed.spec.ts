// ABS-530 — the shared-password access gate is gone.
//
// The gate was a friends-and-family leftover: one password per gate
// (`DEMO_PASSWORD` / `ADMIN_PASSWORD`), handed out by hand, minting a
// bare `abs_demo=1` / `abs_admin=1` cookie via `POST /api/access` and
// checked by `web/proxy.ts` whenever Clerk keys were absent. It could
// not distinguish between two admins and left no audit trail.
//
// Deleting it is easy to half-do — the page and route can go while a
// cookie check quietly survives in the middleware, which would leave a
// forged cookie as a working bypass. So this spec asserts the removal
// from both ends:
//
//   (a) the two URLs are gone (404), so nothing is merely un-linked;
//   (b) minting the cookies by hand does NOT get an anonymous visitor
//       into /app or /admin — the deleted branch is really deleted;
//   (c) /admin/* still admits an allowlisted Clerk user, and 404s a
//       signed-in one who isn't on the list.
//
// (c) matters because /admin was the surface the gate used to cover.
// Removing a gate and losing its replacement in the same change would
// otherwise look identical to "the tests still pass". The deny half is
// also covered by smoke/08-admin-gated.spec.ts, which accepts any of
// 4xx / redirect / deny-copy; here we pin the exact 404 the ticket
// asks for, plus the allow half that nothing else covers.

import { expect, test as base } from "@playwright/test";

import { test } from "../fixtures/test-env";

const E2E_BASE_URL =
  process.env.E2E_BASE_URL || "http://localhost:3001";

test.describe("ABS-530: the /access gate is removed", () => {
  // `request` is an API context independent of the signed-in browser
  // context, and it doesn't follow redirects into hiding a 3xx.
  test("(a) /access and /api/access are 404", async ({ request }) => {
    // The page, plus the query form the old redirect used to send.
    for (const path of ["/access", "/access?gate=admin"]) {
      const res = await request.get(path, { maxRedirects: 0 });
      expect(res.status(), `GET ${path} must be 404`).toBe(404);
    }

    // The POST handler. The old one answered 401 on a bad password and
    // 503 when the env var was unset, so either of those would mean it
    // is still mounted.
    for (const gate of ["demo", "admin"]) {
      const res = await request.post("/api/access", {
        data: { gate, password: "anything" },
        maxRedirects: 0,
      });
      expect(
        res.status(),
        `POST /api/access {gate:"${gate}"} must be 404, not 401/503 — a non-404 means the route is still mounted`,
      ).toBe(404);
    }
  });

  test("(b) robots.txt no longer disallows /access", async ({ request }) => {
    // A Disallow for a 404 is harmless but stale; the entry going away
    // is how we know app/robots.ts was updated along with the route.
    const body = await (await request.get("/robots.txt")).text();
    expect(body).not.toMatch(/^Disallow:\s*\/access$/im);
  });

  test("(c) an allowlisted admin still reaches /admin/invites", async ({
    page,
  }) => {
    // The shared fixture signs us in as the demo user, which
    // e2e-up.sh puts in ADVISOR_ADMIN_CLERK_USER_IDS. A rendered page
    // rather than a 404 proves the Clerk allowlist — the gate's
    // replacement — is carrying the load the abs_admin cookie used to.
    const res = await page.goto(`${E2E_BASE_URL}/admin/invites`);
    expect(res?.status(), "allowlisted admin must not get a 404").toBe(200);
    await expect(page).toHaveURL(/\/admin\/invites/);
    await expect(page.getByText(/INVITE REQUESTS/i)).toBeVisible();
  });
});

// These need a context with NO auto-minted identity, so they use the
// bare Playwright `test` rather than the shared fixture (which signs
// every context in before the first navigation).
base.describe("ABS-530: forging the old cookies buys nothing", () => {
  base("abs_demo / abs_admin do not open /app or /admin", async ({
    browser,
  }) => {
    const context = await browser.newContext();
    const host = new URL(E2E_BASE_URL).hostname;
    // Exactly what POST /api/access used to set.
    await context.addCookies(
      ["abs_demo", "abs_admin"].map((name) => ({
        name,
        value: "1",
        domain: host,
        path: "/",
        httpOnly: true,
        secure: false,
        sameSite: "Lax" as const,
      })),
    );

    const page = await context.newPage();
    try {
      // Anonymous, so clerkMiddleware must bounce us to /sign-in
      // regardless of the cookies.
      await page.goto(`${E2E_BASE_URL}/app`);
      await expect(
        page,
        "abs_demo must not admit an anonymous visitor to /app",
      ).toHaveURL(/\/sign-in/);

      // The abs_admin cookie was previously the entire /admin check.
      await page.goto(`${E2E_BASE_URL}/admin/invites`);
      await expect(
        page,
        "abs_admin must not admit an anonymous visitor to /admin",
      ).toHaveURL(/\/sign-in/);
    } finally {
      await context.close();
    }
  });

  base("a signed-in non-admin gets a 404 on /admin", async ({ browser }) => {
    const context = await browser.newContext();
    const host = new URL(E2E_BASE_URL).hostname;
    // A valid Clerk-mock session for someone NOT in
    // ADVISOR_ADMIN_CLERK_USER_IDS. No JWT cookie needed: proxy.ts
    // rejects on the allowlist before any backend call happens.
    await context.addCookies([
      {
        name: "abs_test_sub_user_id",
        value: `abs530-nonadmin-${Date.now().toString(36)}`,
        domain: host,
        path: "/",
        sameSite: "Lax" as const,
      },
    ]);

    const page = await context.newPage();
    try {
      const res = await page.goto(`${E2E_BASE_URL}/admin/invites`);
      // 404, not 403 — proxy.ts deliberately doesn't confirm /admin
      // exists to a signed-in stranger.
      expect(
        res?.status(),
        "a signed-in non-admin must get 404 on /admin",
      ).toBe(404);
    } finally {
      await context.close();
    }
  });
});
