// Which requests skip Clerk's middleware entirely (ABS-541).
//
// Production runs a Clerk *development* instance. Before Clerk hands a
// request to our callback in proxy.ts, a development instance checks for
// its "dev browser" cookie, and if it is missing on a browser-style request
// (`Accept: text/html`) it answers with a 307 to
// https://<instance>.clerk.accounts.dev/v1/client/handshake — a different
// website. The `if (!isProtectedRoute(req)) return;` inside our callback
// never gets a say; the redirect has already been sent.
//
// Real browsers follow the redirect, collect the cookie and bounce back, so
// nobody noticed. Crawlers carry no cookies and never come back. Googlebot's
// indexing fetch of `/` on 2026-09-11 got exactly this 307, and every public
// page did the same. Search Console's live test did not, because it asks
// for `*/*` rather than `text/html` — which is why "URL is available to
// Google" coexisted with zero indexed pages.
//
// The fix is to not run Clerk where it has no job. The marketing pages
// render without it: the root <ClerkProvider> has no `dynamic` prop, so it
// never reads request auth on the server, and the top nav's signed-in state
// comes from Clerk's client-side hooks, not from the middleware.
//
// What deliberately stays on Clerk:
//   * /cases, /billing — account pages that call auth() on the server
//     (lib/advisor-auth.ts), which throws without the middleware.
//   * /sign-in, /sign-up — Clerk's own flows, including the handshake a
//     real user needs.
//   * /app, /admin, /cases/new, /api — protected or authenticated.
//   * anything not listed here, by default. A page that should skip Clerk
//     has to be added on purpose.

import { PUBLIC_PATHS } from "./public-routes";

// Crawler metadata routes. Google asks for these with `text/plain` first
// and gets a 200 today, but nothing about them needs Clerk either, and a
// crawler that does send `text/html` should not be sent off-site for them.
const CRAWLER_META_PATHS = ["/robots.txt", "/sitemap.xml"];

const BYPASS = new Set<string>([...PUBLIC_PATHS, ...CRAWLER_META_PATHS]);

/**
 * True when a request for this pathname should never reach Clerk's
 * middleware. Exact matches only: `/pricing` skips Clerk, `/pricing/x`
 * does not.
 */
export function bypassesClerk(pathname: string): boolean {
  return BYPASS.has(pathname);
}
