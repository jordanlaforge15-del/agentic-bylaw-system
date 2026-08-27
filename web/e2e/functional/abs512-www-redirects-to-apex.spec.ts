// Functional: www redirects to the apex host (ABS-512).
//
// Caddy terminates TLS in front of both services, and until this change it
// had site blocks only for the apex and `api.` hostnames. Once the `www`
// DNS record lands (ABS-513), a request to www would hit Caddy with a host
// it has no block for — a TLS error, not a redirect. That splits the site
// across two hostnames for anyone who types (or links) the www form.
//
// Caddy is deployment infrastructure and is not part of the e2e stack
// (Playwright talks to `next dev` directly, with no proxy in front), so
// there is no live request that can exercise the redirect here. What this
// spec does instead is guard the two things that actually go wrong:
//
//   1. The redirect rule regressing — dropped, changed to a 302, or
//      losing `{uri}` so every deep link collapses onto the home page.
//      Asserted by parsing the real Caddyfile that ships to production.
//
//   2. The redirect target drifting away from the origin the app calls
//      canonical. Caddy sending traffic to the apex while the app emits
//      www canonicals (or vice versa) would be a redirect loop or a
//      duplicate-content split. Asserted against web/lib/site-url.ts's
//      production fallback, the compose default, and the canonical the
//      running app actually renders.
//
// The prod-side confirmation (`curl -sI https://www...`) stays a manual
// operator step — it needs DNS and a real ACME certificate.

import fs from "node:fs";
import path from "node:path";

import { expect, test } from "../fixtures/test-env";

const REPO_ROOT = path.resolve(__dirname, "..", "..", "..");
const CADDYFILE = fs.readFileSync(path.join(REPO_ROOT, "Caddyfile"), "utf-8");
const COMPOSE = fs.readFileSync(
  path.join(REPO_ROOT, "docker-compose.production.yml"),
  "utf-8",
);
const SITE_URL_TS = fs.readFileSync(
  path.join(REPO_ROOT, "web", "lib", "site-url.ts"),
  "utf-8",
);

/**
 * Split the Caddyfile into its top-level site blocks, keyed by address.
 *
 * Tracks brace depth so nested directives (`header { … }`, `rate_limit {
 * zone … { … } }`) stay inside their parent block, and skips the leading
 * global options block, which has no address.
 */
function siteBlocks(caddyfile: string): Map<string, string> {
  const blocks = new Map<string, string>();
  let address = "";
  let body: string[] = [];
  let depth = 0;

  for (const rawLine of caddyfile.split("\n")) {
    const line = rawLine.trim();
    if (depth === 0) {
      // A site block opens with `<address…> {`. The global options block
      // is a bare `{`, which leaves the address empty and is dropped.
      if (line.endsWith("{")) {
        address = line.slice(0, -1).trim();
        body = [];
        depth = 1;
      }
      continue;
    }
    depth += (line.match(/{/g)?.length ?? 0) - (line.match(/}/g)?.length ?? 0);
    if (depth === 0) {
      if (address) blocks.set(address, body.join("\n"));
      address = "";
      continue;
    }
    body.push(line);
  }
  return blocks;
}

const BLOCKS = siteBlocks(CADDYFILE);
const APEX = "agenticbylawsystems.com";
const WWW = `www.${APEX}`;

/** Security headers every public site block is expected to carry. */
const SECURITY_HEADERS = [
  'Strict-Transport-Security "max-age=31536000; includeSubDomains"',
  'X-Content-Type-Options "nosniff"',
  'X-Frame-Options "DENY"',
  'Referrer-Policy "strict-origin-when-cross-origin"',
  "-Server",
];

test.describe("www → apex redirect (ABS-512)", () => {
  test("Caddy answers for the www hostname", () => {
    // Without a block for this address Caddy has no certificate and no
    // route for www — the request fails at TLS, before any redirect.
    expect([...BLOCKS.keys()]).toContain(WWW);
  });

  test("www 301s to the apex, preserving path and query", () => {
    const www = BLOCKS.get(WWW) ?? "";
    const redir = www
      .split("\n")
      .find((line) => line.startsWith("redir "))
      ?.trim();

    expect(redir, "www block must contain a redir directive").toBeTruthy();
    // `{uri}` is path + query. `{path}` alone silently drops query strings
    // (utm tags, share params); a bare target collapses every deep link
    // onto the home page. `permanent` is the 301 the AC calls for — a 302
    // leaves search engines indexing both hostnames indefinitely.
    expect(redir).toBe(`redir https://${APEX}{uri} permanent`);
  });

  test("the www block redirects only — it never serves the app", () => {
    const www = BLOCKS.get(WWW) ?? "";
    // A reverse_proxy here would make www a second live origin rather than
    // a redirect, which is the duplicate-content problem this block exists
    // to remove.
    expect(www).not.toContain("reverse_proxy");
  });

  test("the redirect response carries the same security headers", () => {
    const www = BLOCKS.get(WWW) ?? "";
    const apex = BLOCKS.get(APEX) ?? "";
    for (const header of SECURITY_HEADERS) {
      expect(apex, `apex block lost ${header}`).toContain(header);
      // The 301 is itself a response a browser sees; without HSTS on it,
      // a first-visit http→www request has no pinning for the hop.
      expect(www, `www block missing ${header}`).toContain(header);
    }
  });

  test("the apex still proxies to the web service", () => {
    // Guards against the www block being added by editing the apex block
    // instead of adding a new one.
    expect(BLOCKS.get(APEX) ?? "").toContain("reverse_proxy web:3000");
  });

  test("the redirect target matches the origin the app calls canonical", () => {
    // Three independent declarations of the production origin. If any one
    // drifts, users bounce between hostnames or loop.
    expect(SITE_URL_TS).toContain(`"https://${APEX}"`);
    expect(COMPOSE).toContain(`SITE_URL: \${SITE_URL:-https://${APEX}}`);
    expect(BLOCKS.get(WWW) ?? "").toContain(`https://${APEX}{uri}`);
  });

  test("the running app emits apex-shaped canonicals, not www", async ({
    page,
  }) => {
    // The live half of the check: whatever origin the app is served from,
    // its canonical must not carry a `www.` label. A www canonical would
    // point straight at the hostname Caddy redirects away from, so every
    // crawler hit would take an extra hop — or loop, if the app ever
    // learned to serve www itself.
    await page.goto("/pricing", { waitUntil: "domcontentloaded" });
    const canonical = await page
      .locator('head link[rel="canonical"]')
      .first()
      .getAttribute("href");

    expect(canonical).toBeTruthy();
    const host = new URL(canonical as string).hostname;
    expect(host.startsWith("www.")).toBe(false);
  });
});
