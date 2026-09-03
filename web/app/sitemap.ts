// /sitemap.xml — Next.js Metadata Route (ABS-507).
//
// The route list itself lives in web/lib/public-routes.ts (ABS-527), so
// this file and the specs that assert on it read the same array instead
// of each keeping a copy that can drift. Rationale for what is in and
// out of that list is documented there.
//
// No `lastModified`: we have no honest per-page mtime to report (the
// git SHA of a component tells us nothing about whether the copy
// changed), and a build-stamped `new Date()` on every page would just
// tell Google "every page changed" on every deploy, which trains it to
// ignore the field.

import type { MetadataRoute } from "next";
import { absoluteUrl } from "../lib/site-url";
import { PUBLIC_ROUTES } from "../lib/public-routes";

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
