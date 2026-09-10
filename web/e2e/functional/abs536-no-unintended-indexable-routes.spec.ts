// ABS-536 — no route is crawlable and indexable unless we meant it,
// and no route we *do* want indexed is secretly suppressed.
//
// Two real production defects sit behind this spec.
//
// 1. `/submissions/<anything>` answered 200 for every string, with no
//    `robots` meta tag, no robots.txt entry, and the generic root
//    title. An unbounded space of near-identical thin pages. On a
//    domain with no crawl history that is how a small crawl allowance
//    gets spent on nothing while /pricing sits at "Discovered -
//    currently not indexed". `/e2e-throw` was crawlable for the same
//    reason.
//
// 2. The home page was excluded from Google's index for months by an
//    `X-Robots-Tag: noindex` HTTP *response header* (ABS-508). Every
//    check we had parsed the rendered HTML, so every check came back
//    clean while the site quietly held zero indexed pages. The source
//    was never found — it is in no image, no Caddy config and no
//    commit — which is precisely why the guard here is a detector
//    rather than a fix. A header-borne `noindex` is exactly as fatal
//    as a meta-tag one and strictly harder to see.
//
// So this spec asserts on what a crawler actually receives — status,
// `X-Robots-Tag`, and the `robots` meta tag — for every route derived
// from the filesystem, not from any hand-maintained list. Add a route
// and forget to classify it and this fails; add a `noindex` to a
// public route and this fails too.
//
// Related: abs527-sitemap-covers-indexable-routes.spec.ts does the
// mirror-image check for the (marketing) group only, comparing routes
// to /sitemap.xml by reading page *source*. This one covers the whole
// app/ tree and reads live *responses*.

import fs from "node:fs";
import path from "node:path";
import { expect, test } from "../fixtures/test-env";
import { PUBLIC_PATHS } from "../../lib/public-routes";

const APP_DIR = path.resolve(__dirname, "../../app");

// Value substituted for a dynamic segment so the route can actually be
// fetched. Deliberately a well-formed-looking id that matches nothing:
// the point is that a crawler hitting a junk id must not receive an
// indexable page, which is the /submissions/[id] defect exactly.
const DYNAMIC_PROBE = "abs536-probe";

/**
 * Every route the app serves, derived by walking app/ for page.tsx.
 *
 * Segment rules, matching Next's App Router:
 *   * `(group)`      — route group, contributes no URL segment.
 *   * `[[...slug]]`  — optional catch-all, matches the bare parent
 *                      path, so the segment is dropped.
 *   * `[...slug]`    — required catch-all, needs one segment.
 *   * `[id]`         — dynamic, needs one segment.
 * Private folders (`_foo`) and parallel/intercepting routes (`@foo`,
 * `(.)foo`) are skipped — none exist today, but they must not silently
 * become URL segments if someone adds one.
 */
function appRoutes(): string[] {
  const routes: string[] = [];

  function walk(dir: string, segments: string[]) {
    if (fs.existsSync(path.join(dir, "page.tsx"))) {
      routes.push("/" + segments.join("/"));
    }
    for (const entry of fs.readdirSync(dir, { withFileTypes: true })) {
      if (!entry.isDirectory()) continue;
      const name = entry.name;
      if (name.startsWith("_") || name.startsWith("@") || name === "node_modules") {
        continue;
      }
      let next = segments;
      if (name.startsWith("(") && name.endsWith(")")) {
        next = segments; // route group: no URL segment
      } else if (name.startsWith("[[") && name.endsWith("]]")) {
        next = segments; // optional catch-all: bare parent path
      } else if (name.startsWith("[")) {
        next = [...segments, DYNAMIC_PROBE];
      } else {
        next = [...segments, name];
      }
      walk(path.join(dir, name), next);
    }
  }

  walk(APP_DIR, []);
  // "/" comes out of the walk as "" -> "/" already; normalise doubles.
  return [...new Set(routes.map((r) => (r === "/" ? "/" : r.replace(/\/+$/, ""))))].sort();
}

/** The `Disallow:` prefixes the live /robots.txt actually publishes. */
async function disallowedPrefixes(
  request: import("@playwright/test").APIRequestContext,
): Promise<string[]> {
  const res = await request.get("/robots.txt");
  expect(res.status(), "/robots.txt must be served").toBe(200);
  return (await res.text())
    .split("\n")
    .map((l) => l.trim())
    .filter((l) => /^Disallow:/i.test(l))
    .map((l) => l.replace(/^Disallow:\s*/i, "").trim())
    .filter((v) => v.length > 0);
}

function isDisallowed(route: string, prefixes: string[]): boolean {
  return prefixes.some((p) => route === p || route.startsWith(p + "/"));
}

/** A `robots` meta tag carrying noindex, if the body has one. */
function metaNoindex(html: string): boolean {
  const m = html.match(
    /<meta[^>]+name=["']robots["'][^>]*content=["']([^"']*)["']/i,
  );
  return m ? /noindex/i.test(m[1]) : false;
}

test.describe("crawlable surface (ABS-536)", () => {
  test("every route is either public, disallowed, gated, or noindexed", async ({
    request,
  }) => {
    const routes = appRoutes();
    // Guard against the walk finding nothing and every assertion below
    // passing vacuously.
    expect(routes.length, `no routes found under ${APP_DIR}`).toBeGreaterThan(10);

    const prefixes = await disallowedPrefixes(request);
    expect(prefixes.length, "robots.txt publishes no Disallow rules").toBeGreaterThan(0);

    const unclassified: string[] = [];

    for (const route of routes) {
      if (PUBLIC_PATHS.includes(route)) continue; // asserted separately below
      if (isDisallowed(route, prefixes)) continue;

      const res = await request.get(route, { maxRedirects: 0 });
      const status = res.status();

      // A redirect or an error gives a crawler nothing to index. This
      // is how the auth-gated routes (307 to /sign-in) pass.
      if (status >= 300) continue;

      const header = res.headers()["x-robots-tag"] ?? "";
      if (/noindex/i.test(header)) continue;
      if (metaNoindex(await res.text())) continue;

      unclassified.push(`${route} (HTTP ${status})`);
    }

    expect(
      unclassified,
      "these routes return an indexable 200 but are neither in PUBLIC_ROUTES " +
        "(web/lib/public-routes.ts) nor disallowed in robots.txt nor marked " +
        "noindex. Either add them to PUBLIC_ROUTES if they should rank, or " +
        "give them `robots: { index: false, follow: false }` and a robots.txt " +
        `Disallow entry if they should not:\n  ${unclassified.join("\n  ")}`,
    ).toEqual([]);
  });

  // The ABS-508 regression guard. The home page spent months excluded
  // from Google by a response header nobody could see, behind a fully
  // green suite.
  test("no public route is suppressed by X-Robots-Tag or a robots meta tag", async ({
    request,
  }) => {
    const suppressed: string[] = [];

    for (const route of PUBLIC_PATHS) {
      const res = await request.get(route, { maxRedirects: 0 });
      expect(res.status(), `${route} must serve 200 to a crawler`).toBe(200);

      const header = res.headers()["x-robots-tag"];
      if (header) suppressed.push(`${route} — X-Robots-Tag: ${header}`);
      if (metaNoindex(await res.text())) {
        suppressed.push(`${route} — <meta name="robots" content="noindex">`);
      }
    }

    expect(
      suppressed,
      "routes listed in the sitemap are telling Google not to index them. " +
        "This is the ABS-508 failure mode: the site served " +
        "`X-Robots-Tag: noindex` site-wide for months and every HTML-only " +
        `check passed anyway:\n  ${suppressed.join("\n  ")}`,
    ).toEqual([]);
  });

  test("the two routes ABS-536 closed are disallowed and noindexed", async ({
    request,
  }) => {
    const prefixes = await disallowedPrefixes(request);

    for (const route of ["/submissions", "/e2e-throw"]) {
      expect(
        isDisallowed(route, prefixes),
        `robots.txt must Disallow ${route}`,
      ).toBe(true);
    }

    // The unbounded-id case specifically: a junk submission id must not
    // come back indexable.
    const res = await request.get(`/submissions/${DYNAMIC_PROBE}`, {
      maxRedirects: 0,
    });
    if (res.status() < 300) {
      expect(
        metaNoindex(await res.text()),
        "/submissions/<junk id> returns 200 and must carry a noindex meta tag",
      ).toBe(true);
    }
  });
});
