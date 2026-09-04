// Single source of truth for "which routes are public and indexable"
// (ABS-527).
//
// This list used to exist in three hand-maintained copies: PUBLIC_ROUTES
// in app/sitemap.ts, PUBLIC_PATHS in the ABS-507 spec, and ROUTES in the
// ABS-509 spec. They drifted — ABS-509 shipped /signup as a real,
// indexable marketing page while ABS-507's sitemap omitted it, so the
// one page that converts private-beta traffic was crawlable but absent
// from the seed list a crawler is handed. Both suites still passed,
// because each compared its own copy to another copy.
//
// One list now. sitemap.ts renders it, and both specs import it.
//
// What belongs here: routes an anonymous visitor can fully render and
// that we want in search results. What does not:
//   * /app, /admin, /cases/new — 307 for anonymous visitors (see
//     isProtectedRoute in proxy.ts). Listing a redirect in a sitemap is
//     a Search Console error, not just noise.
//   * /sign-in, /sign-up, /access — credential gates. Reachable, but
//     worthless as a search result. Note /sign-up (the Clerk gate) is a
//     different route from /signup (the invite-request marketing page)
//     — see the comment on DISALLOWED_PATHS in app/robots.ts.
//   * /billing, /cases — account surfaces. They carry explicit
//     `robots: { index: false, follow: false }` in their own metadata
//     and are excluded here deliberately.
//
// `priority` is a relative hint within this one sitemap: the home page
// leads, the pages that convert (pricing, signup, coverage) sit just
// under it, and the legal boilerplate trails. `changeFrequency` mirrors
// how often each page actually moves — /changelog churns, /terms does
// not.

/** A `changeFrequency` value as sitemap.xml accepts it. */
export type ChangeFrequency =
  | "always"
  | "hourly"
  | "daily"
  | "weekly"
  | "monthly"
  | "yearly"
  | "never";

export type PublicRoute = {
  path: string;
  changeFrequency: ChangeFrequency;
  priority: number;
};

export const PUBLIC_ROUTES: PublicRoute[] = [
  { path: "/", changeFrequency: "weekly", priority: 1 },
  { path: "/pricing", changeFrequency: "weekly", priority: 0.9 },
  // The invite-request page for the private beta. It converts, so it
  // sits beside /pricing.
  { path: "/signup", changeFrequency: "monthly", priority: 0.9 },
  { path: "/coverage", changeFrequency: "weekly", priority: 0.8 },
  { path: "/about", changeFrequency: "monthly", priority: 0.7 },
  { path: "/support", changeFrequency: "monthly", priority: 0.6 },
  { path: "/changelog", changeFrequency: "weekly", priority: 0.5 },
  { path: "/privacy", changeFrequency: "yearly", priority: 0.3 },
  { path: "/terms", changeFrequency: "yearly", priority: 0.3 },
];

/** Just the paths, in sitemap order. */
export const PUBLIC_PATHS: string[] = PUBLIC_ROUTES.map((r) => r.path);
