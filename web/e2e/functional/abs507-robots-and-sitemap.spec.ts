// ABS-507 — /robots.txt and /sitemap.xml are served by the Next app.
//
// Both URLs used to 404, which meant no `Sitemap:` pointer and no seed
// list of URLs for a crawler. With zero inbound links, nothing had ever
// pointed a crawler at the site.
//
// What the acceptance criteria actually require, and what each test
// below pins down:
//   (a) /robots.txt   → 200, text/plain, an absolute `Sitemap:` line.
//   (b) /sitemap.xml  → 200, application/xml, a <urlset> whose <loc>
//                       values are absolute.
//   (c) the sitemap lists every public marketing route and none of the
//       gated ones.
//
// Both routes are fetched through the API-request context rather than
// page.goto so we assert on the raw status + content-type the crawler
// sees, not on whatever the browser decides to render.

import { test, expect } from "../fixtures/test-env";
// The public route list is imported, not copied (ABS-527). It used to be
// a hand-maintained duplicate here; it drifted from the sitemap's copy
// and /signup fell through the gap. Drift-detection now lives in
// abs527-sitemap-covers-indexable-routes.spec.ts, which derives the
// expected set from the filesystem instead of from another list.
import { PUBLIC_PATHS } from "../../lib/public-routes";

// Auth-gated / operational surfaces. /app, /admin and /cases/new 307
// for anonymous visitors; the rest are credential dead-ends. Note
// `/sign-up` is the Clerk gate, not the public `/signup` invite page.
const GATED_PATHS = [
  "/app",
  "/admin",
  "/cases/new",
  "/sign-in",
  "/sign-up",
  "/api",
];

/** Pull the <loc> values out of a sitemap body. */
function locs(xml: string): string[] {
  return [...xml.matchAll(/<loc>([^<]+)<\/loc>/g)].map((m) => m[1].trim());
}

test.describe("robots.txt + sitemap.xml (ABS-507)", () => {
  test("(a) /robots.txt is 200 text/plain and advertises an absolute sitemap", async ({
    request,
  }) => {
    const res = await request.get("/robots.txt");
    expect(res.status()).toBe(200);
    expect(res.headers()["content-type"]).toContain("text/plain");

    const body = await res.text();

    // The Sitemap: line is the entire point of the ticket.
    const sitemapLine = body
      .split("\n")
      .map((l) => l.trim())
      .find((l) => /^Sitemap:/i.test(l));
    expect(sitemapLine, "robots.txt must contain a Sitemap: line").toBeTruthy();

    const sitemapUrl = sitemapLine!.replace(/^Sitemap:\s*/i, "");
    expect(sitemapUrl, "Sitemap: URL must be absolute").toMatch(/^https?:\/\//);
    expect(sitemapUrl).toMatch(/\/sitemap\.xml$/);

    // Crawl-everything for all agents, minus the gated surfaces.
    expect(body).toMatch(/User-Agent:\s*\*/i);
    expect(body).toMatch(/^Allow:\s*\/$/im);
    for (const path of ["/app", "/admin", "/api", "/sign-in", "/sign-up"]) {
      expect(
        body,
        `robots.txt must disallow ${path}`,
      ).toMatch(new RegExp(`^Disallow:\\s*${path}$`, "im"));
    }

    // ...and must NOT disallow the public invite-request page, which
    // differs from the Clerk gate by a single hyphen (ABS-527).
    expect(
      body,
      "robots.txt must not disallow /signup (the public invite page)",
    ).not.toMatch(/^Disallow:\s*\/signup$/im);
  });

  test("(b) the advertised sitemap URL resolves to a valid urlset", async ({
    request,
  }) => {
    // Follow the pointer robots.txt hands out rather than hardcoding
    // /sitemap.xml — that is the path a crawler actually takes, so a
    // broken pointer fails here instead of passing silently.
    const robots = await (await request.get("/robots.txt")).text();
    const sitemapUrl = robots
      .split("\n")
      .map((l) => l.trim())
      .find((l) => /^Sitemap:/i.test(l))!
      .replace(/^Sitemap:\s*/i, "");

    const res = await request.get(sitemapUrl);
    expect(res.status()).toBe(200);
    expect(res.headers()["content-type"]).toContain("application/xml");

    const xml = await res.text();
    expect(xml).toContain("<urlset");
    expect(xml).toContain("http://www.sitemaps.org/schemas/sitemap/0.9");

    const urls = locs(xml);
    expect(urls.length).toBeGreaterThan(0);
    for (const url of urls) {
      expect(url, "<loc> values must be absolute").toMatch(/^https?:\/\/[^/]+/);
    }
  });

  test("(c) sitemap lists every public route and no gated route", async ({
    request,
  }) => {
    const res = await request.get("/sitemap.xml");
    expect(res.status()).toBe(200);

    const urls = locs(await res.text());
    // `new URL("https://host").pathname` is "/", which lines up with
    // the "/" entry in PUBLIC_PATHS.
    const paths = urls.map((u) => new URL(u).pathname.replace(/(.)\/$/, "$1"));

    for (const path of PUBLIC_PATHS) {
      expect(paths, `sitemap must list ${path}`).toContain(path);
    }
    for (const path of GATED_PATHS) {
      expect(
        paths.some((p) => p === path || p.startsWith(`${path}/`)),
        `sitemap must not list ${path}`,
      ).toBe(false);
    }
    // Nothing beyond the public set — catches a future route being
    // added to the sitemap without a deliberate decision.
    expect(paths.sort()).toEqual([...PUBLIC_PATHS].sort());
  });
});
