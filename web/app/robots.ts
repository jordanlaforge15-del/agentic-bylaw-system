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
