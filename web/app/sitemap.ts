// /sitemap.xml — Next.js Metadata Route (ABS-507).
//
// Public marketing routes only. Everything a crawler can actually
// render anonymously, and nothing else:
//
//   * /app, /admin, /cases/new — 307 for anonymous visitors (see
//     isProtectedRoute in proxy.ts). Listing a redirect in a sitemap
//     is a Search Console error, not just noise.
//   * /sign-in, /sign-up, /access — credential gates. Reachable, but
//     worthless as a search result.
//
// `priority` is a relative hint within this one sitemap: the home page
// leads, the two pages that convert (pricing, coverage) sit just under
// it, and the legal boilerplate trails. `changeFrequency` mirrors how
// often each page actually moves — /changelog churns, /terms does not.
//
// No `lastModified`: we have no honest per-page mtime to report (the
// git SHA of a component tells us nothing about whether the copy
// changed), and a build-stamped `new Date()` on every page would just
// tell Google "all eight pages changed" on every deploy, which trains
// it to ignore the field.

import type { MetadataRoute } from "next";
import { absoluteUrl } from "../lib/site-url";

type PublicRoute = {
  path: string;
  changeFrequency: MetadataRoute.Sitemap[number]["changeFrequency"];
  priority: number;
};

const PUBLIC_ROUTES: PublicRoute[] = [
  { path: "/", changeFrequency: "weekly", priority: 1 },
  { path: "/pricing", changeFrequency: "weekly", priority: 0.9 },
  { path: "/coverage", changeFrequency: "weekly", priority: 0.8 },
  { path: "/about", changeFrequency: "monthly", priority: 0.7 },
  { path: "/support", changeFrequency: "monthly", priority: 0.6 },
  { path: "/changelog", changeFrequency: "weekly", priority: 0.5 },
  { path: "/privacy", changeFrequency: "yearly", priority: 0.3 },
  { path: "/terms", changeFrequency: "yearly", priority: 0.3 },
];

// Evaluated per request rather than baked at build time, so SITE_URL
// set on the container is honoured without rebuilding the image.
export const dynamic = "force-dynamic";

export default function sitemap(): MetadataRoute.Sitemap {
  return PUBLIC_ROUTES.map(({ path, changeFrequency, priority }) => ({
    url: absoluteUrl(path),
    changeFrequency,
    priority,
  }));
}
