// ABS-527 — the sitemap must cover exactly the indexable marketing routes.
//
// The bug this catches: /signup shipped as a real, indexable marketing
// page (ABS-509 gave it a title and description and no `robots:
// { index: false }`) while the sitemap's route list (ABS-507) never
// learned about it. Crawlable but undiscoverable — and with no inbound
// links, the sitemap is the only seed list a crawler gets.
//
// Both existing suites passed anyway, because each hand-maintained its
// own copy of "the public routes" and only ever compared one copy to
// another. A route missing from every copy was invisible.
//
// So this spec does not read any route list. It walks the filesystem —
// Playwright specs run in Node, so `fs.readdirSync` on
// web/app/(marketing) is available — decides for each route whether its
// own metadata opts out of indexing, and holds the live /sitemap.xml to
// that. Add a marketing page and forget the sitemap, and this fails; add
// one with `robots: { index: false }` and it must stay out.

import fs from "node:fs";
import path from "node:path";
import { expect, test } from "../fixtures/test-env";

const MARKETING_DIR = path.resolve(__dirname, "../../app/(marketing)");

// Routes deliberately not subject to the metadata check:
//   /login — app/(marketing)/login/page.tsx is a bare
//     `redirect("/sign-in")`. It renders nothing and exports no
//     metadata, so the index:false probe below would classify it as
//     "indexable" and demand a sitemap entry for a route that only ever
//     302s. Excluded by name rather than left to fall through.
const EXCLUDED_ROUTES = new Set(["/login"]);

/** Every route the (marketing) group serves, derived from the tree. */
function marketingRoutes(): string[] {
  const routes: string[] = [];
  // The group's own page.tsx is the home page.
  if (fs.existsSync(path.join(MARKETING_DIR, "page.tsx"))) routes.push("/");
  for (const entry of fs.readdirSync(MARKETING_DIR, { withFileTypes: true })) {
    if (!entry.isDirectory()) continue;
    if (!fs.existsSync(path.join(MARKETING_DIR, entry.name, "page.tsx"))) continue;
    routes.push(`/${entry.name}`);
  }
  return routes.filter((r) => !EXCLUDED_ROUTES.has(r)).sort();
}

/**
 * True when the route's own page.tsx or layout.tsx opts out of indexing
 * via `robots: { index: false }` in its exported metadata. A source-text
 * probe rather than a module import: importing a Next page pulls in the
 * whole server component graph, and the metadata export is a static
 * object literal in every marketing route today.
 */
function optsOutOfIndexing(route: string): boolean {
  const dir = route === "/" ? MARKETING_DIR : path.join(MARKETING_DIR, route.slice(1));
  return ["page.tsx", "layout.tsx"].some((file) => {
    const full = path.join(dir, file);
    if (!fs.existsSync(full)) return false;
    return /index:\s*false/.test(fs.readFileSync(full, "utf8"));
  });
}

/** Pull the <loc> paths out of a sitemap body, de-trailing-slashed. */
function sitemapPaths(xml: string): string[] {
  return [...xml.matchAll(/<loc>([^<]+)<\/loc>/g)].map((m) =>
    new URL(m[1].trim()).pathname.replace(/(.)\/$/, "$1"),
  );
}

test("every indexable marketing route is in the sitemap, and no opted-out route is", async ({
  request,
}) => {
  const routes = marketingRoutes();
  // Guard against the walk silently finding nothing (wrong path, moved
  // directory) and the assertions below passing vacuously.
  expect(routes.length, `no marketing routes found under ${MARKETING_DIR}`)
    .toBeGreaterThan(5);

  const res = await request.get("/sitemap.xml");
  expect(res.status()).toBe(200);
  const listed = sitemapPaths(await res.text());

  const indexable = routes.filter((r) => !optsOutOfIndexing(r));
  const optedOut = routes.filter((r) => optsOutOfIndexing(r));

  const missing = indexable.filter((r) => !listed.includes(r));
  expect(
    missing,
    `indexable marketing routes absent from /sitemap.xml: ${missing.join(", ")}. ` +
      "Either add them to PUBLIC_ROUTES in web/lib/public-routes.ts, or give " +
      "the page `robots: { index: false, follow: false }` if it should not be found.",
  ).toEqual([]);

  const leaked = optedOut.filter((r) => listed.includes(r));
  expect(
    leaked,
    `routes marked \`index: false\` but still listed in /sitemap.xml: ${leaked.join(", ")}`,
  ).toEqual([]);
});
