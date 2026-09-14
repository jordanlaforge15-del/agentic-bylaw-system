// ABS-541 — public pages never reach Clerk's middleware.
//
// The production bug: a Clerk *development* instance answers any cookieless
// browser-style request (`Accept: text/html`) with a 307 to its handshake on
// clerk.accounts.dev, before our proxy callback runs. Every marketing page did
// this to Googlebot's indexing fetch, which is why nothing was indexed while
// every curl-based check of the site came back 200.
//
// What this spec can and cannot prove. The e2e stack runs a Clerk *mock*, not
// a real instance, so it cannot reproduce the handshake itself. It therefore
// pins down the two things that decide whether the bug can come back:
//
//   (a) the routing decision — lib/clerk-bypass.ts must let every route in
//       PUBLIC_ROUTES (plus robots.txt and sitemap.xml) skip Clerk, and must
//       NOT let any authenticated or Clerk-owned route skip it. Deriving the
//       expected set from PUBLIC_ROUTES means a new marketing page is covered
//       the moment it joins the sitemap;
//   (b) that those routes render, with browser navigation headers, without
//       the middleware — i.e. nothing on them calls server-side auth().
//
// The handshake itself is checked against a real Clerk instance by
// scripts/check-crawler-access.sh, run after deploys.

import { expect, test } from "../fixtures/test-env";
import { PUBLIC_PATHS } from "../../lib/public-routes";
import { bypassesClerk } from "../../lib/clerk-bypass";

// How Googlebot's smartphone crawler asks for a page. The Accept header is the
// part that matters: Clerk only handshakes requests that look like browser
// document navigations.
const CRAWLER_HTML_HEADERS = {
  "User-Agent":
    "Mozilla/5.0 (Linux; Android 6.0.1; Nexus 5X Build/MMB29P) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/139.0.0.0 Mobile Safari/537.36 (compatible; Googlebot/2.1; +http://www.google.com/bot.html)",
  Accept: "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
  "Sec-Fetch-Dest": "document",
  "Sec-Fetch-Mode": "navigate",
  "Sec-Fetch-Site": "none",
};

const CRAWLER_META = ["/robots.txt", "/sitemap.xml"];

// Routes that must keep going through Clerk. Each one either calls auth() on
// the server, is protected, or is one of Clerk's own flows.
const MUST_USE_CLERK = [
  "/app",
  "/app/billing",
  "/admin",
  "/admin/invites",
  "/cases",
  "/cases/new",
  "/billing",
  "/sign-in",
  "/sign-up",
  "/sign-in/factor-one",
  "/api/chat",
  "/submissions/new",
  "/login",
];

test.describe("public pages skip Clerk (ABS-541)", () => {
  test("(a) every public route and crawler file bypasses Clerk", () => {
    expect(PUBLIC_PATHS.length, "PUBLIC_ROUTES is empty").toBeGreaterThan(5);
    const notBypassed = [...PUBLIC_PATHS, ...CRAWLER_META].filter(
      (p) => !bypassesClerk(p),
    );
    expect(
      notBypassed,
      "these public routes still go through Clerk's middleware, which on a " +
        "development instance 307s a crawler to clerk.accounts.dev",
    ).toEqual([]);
  });

  test("(a) authenticated and Clerk-owned routes still go through Clerk", () => {
    const wronglyBypassed = MUST_USE_CLERK.filter((p) => bypassesClerk(p));
    expect(
      wronglyBypassed,
      "these routes would skip Clerk — auth() would throw or protection would " +
        "be lost",
    ).toEqual([]);
  });

  test("(a) the bypass is exact-match, not a prefix", () => {
    // /pricing skipping Clerk must not make /pricing/anything skip it too.
    expect(bypassesClerk("/pricing")).toBe(true);
    expect(bypassesClerk("/pricing/x")).toBe(false);
    expect(bypassesClerk("/app")).toBe(false);
    expect(bypassesClerk("/")).toBe(true);
    expect(bypassesClerk("//")).toBe(false);
  });

  test("(b) public routes render for a crawler requesting HTML, with no redirect", async ({
    request,
  }) => {
    const problems: string[] = [];
    for (const route of [...PUBLIC_PATHS, ...CRAWLER_META]) {
      const res = await request.get(route, {
        headers: CRAWLER_HTML_HEADERS,
        maxRedirects: 0,
      });
      const location = res.headers()["location"];
      if (res.status() !== 200 || location) {
        problems.push(`${route} → ${res.status()}${location ? ` ${location}` : ""}`);
      }
    }
    expect(problems, "public routes that do not serve a crawler a 200").toEqual([]);
  });

  test("(b) a protected route still redirects an anonymous crawler to sign-in", async ({
    request,
  }) => {
    const res = await request.get("/app", {
      headers: CRAWLER_HTML_HEADERS,
      maxRedirects: 0,
    });
    expect(res.status()).toBeGreaterThanOrEqual(300);
    expect(res.status()).toBeLessThan(400);
    expect(res.headers()["location"] ?? "").toContain("sign-in");
  });
});
