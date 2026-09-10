// /robots.txt — Next.js Metadata Route (ABS-507).
//
// Two jobs:
//   1. Keep crawlers out of the auth-gated and operational surfaces.
//      None of these are useful search results: /app and /admin
//      redirect anonymous visitors, /api is machine-only, and the
//      sign-in / sign-up gates are dead ends.
//   2. Advertise the sitemap. This is the whole point of the ticket —
//      with no inbound links, the `Sitemap:` line is the only thing
//      that hands Google a seed list of URLs.
//
// Note on middleware: web/proxy.ts's matcher excludes `_next` and a
// static-extension list that does NOT include `.txt`, so this request
// does run through the proxy. That is harmless — /robots.txt is not in
// isProtectedRoute, so both the Clerk and the no-Clerk branch fall
// straight through to NextResponse.next().

import type { MetadataRoute } from "next";
import { absoluteUrl } from "../lib/site-url";

// Auth-gated and operational surfaces. Kept in sync by hand with
// isProtectedRoute in proxy.ts plus the credential pages; the sitemap
// deliberately lists none of these.
//
// READ THE HYPHEN (ABS-527). `/sign-up` below is the Clerk credential
// gate at app/sign-up/ — a dead end for a crawler. It is NOT `/signup`,
// the public invite-request marketing page at app/(marketing)/signup/,
// which is indexable and listed in the sitemap. One hyphen separates
// them, and mistaking one for the other is exactly what left /signup
// crawlable-but-undiscoverable. Do not add `/signup` here without also
// removing it from PUBLIC_ROUTES in web/lib/public-routes.ts and giving
// its layout `robots: { index: false, follow: false }`.
const DISALLOWED_PATHS = [
  "/app",
  "/admin",
  "/api",
  "/sign-in",
  "/sign-up",
  // ABS-536. /submissions/<anything> returns 200 for every string —
  // `new`, a real id, a bogus uuid, junk — so a crawler can walk an
  // unbounded set of near-identical thin pages. On a domain with no
  // crawl history that is how a tiny crawl allowance gets spent on
  // nothing while /pricing sits at "Discovered - currently not
  // indexed". The subtree also carries `noindex` via its own layout;
  // this line stops the fetch, that one stops the indexing.
  "/submissions",
  // Test-only route: it throws during render so the error-boundary
  // spec can assert the fallback UI. Guarded to render "Not
  // available" when NODE_ENV is production, but it still answers 200
  // there, which is a thin page with the root title. It cannot simply
  // be deleted from the production build — e2e runs `next dev`
  // against this same tree and error-boundaries.spec.ts navigates to
  // it — so it is kept and excluded from crawling instead.
  "/e2e-throw",
];

// Evaluated per request rather than baked at build time, so SITE_URL
// set on the container is honoured without rebuilding the image.
export const dynamic = "force-dynamic";

export default function robots(): MetadataRoute.Robots {
  return {
    rules: {
      userAgent: "*",
      allow: "/",
      disallow: DISALLOWED_PATHS,
    },
    sitemap: absoluteUrl("/sitemap.xml"),
  };
}
