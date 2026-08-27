// Functional: canonical URLs and Open Graph / Twitter card tags (ABS-510).
//
// Before this, the root layout exported only `title` and `description`.
// With no `metadataBase`, Next had no origin to resolve absolute URLs
// against, so there was no <link rel="canonical"> and not one `og:` or
// `twitter:` tag in the production HTML. Link previews on LinkedIn,
// Slack and iMessage rendered as a bare URL — on exactly the channels
// that would produce the site's first inbound links.
//
// Two failures this spec is built to catch:
//
//   1. Relative URLs. If `metadataBase` goes missing, Next emits the
//      canonical and og:image as root-relative paths (or drops them),
//      and every crawler and unfurler ignores them. Every URL assertion
//      below parses the value as an absolute URL and checks the origin.
//
//   2. Canonical inheritance. Next merges `metadata` exports shallowly:
//      a page that does not declare its own `alternates` inherits the
//      root layout's `canonical: "/"` and tells Google it is a duplicate
//      of the home page. The per-route loop asserts a *self*-referencing
//      canonical, so a new marketing page that forgets pageMetadata()
//      fails here rather than silently deindexing itself.
//
// Routes come from PUBLIC_ROUTES in web/lib/public-routes.ts — the same
// list the sitemap renders and the ABS-507/509 specs read (ABS-527).

import { expect, test } from "../fixtures/test-env";
import { PUBLIC_PATHS as ROUTES } from "../../lib/public-routes";
import { OG_IMAGE_PATH } from "../../lib/page-metadata";

/** Read a `<meta property="...">` (Open Graph uses `property`). */
async function ogTag(page: import("@playwright/test").Page, property: string) {
  return page
    .locator(`head meta[property="${property}"]`)
    .first()
    .getAttribute("content");
}

/** Read a `<meta name="...">` (Twitter cards use `name`). */
async function nameTag(page: import("@playwright/test").Page, name: string) {
  return page
    .locator(`head meta[name="${name}"]`)
    .first()
    .getAttribute("content");
}

/** Trailing slashes are cosmetic; "/pricing/" and "/pricing" are one page. */
function normalizePath(pathname: string): string {
  return pathname !== "/" && pathname.endsWith("/")
    ? pathname.slice(0, -1)
    : pathname;
}

test("the home page emits a canonical URL and complete social cards", async ({
  page,
}) => {
  await page.goto("/", { waitUntil: "domcontentloaded" });
  const origin = new URL(page.url()).origin;

  const canonical = await page
    .locator('head link[rel="canonical"]')
    .first()
    .getAttribute("href");
  expect(canonical, "/ should emit <link rel=canonical>").toBeTruthy();
  // Absolute, not "/" — the whole point of metadataBase.
  expect(new URL(canonical!).origin).toBe(origin);
  expect(normalizePath(new URL(canonical!).pathname)).toBe("/");

  // Open Graph. `title`/`description` are asserted non-empty rather than
  // copy-exact so a wording change doesn't fail the build; ABS-509's spec
  // already guards that the copy is unique per route.
  expect(await ogTag(page, "og:type")).toBe("website");
  expect(await ogTag(page, "og:site_name")).toContain("ABS°");
  expect((await ogTag(page, "og:title"))?.trim()).toBeTruthy();
  expect((await ogTag(page, "og:description"))?.trim()).toBeTruthy();

  const ogUrl = await ogTag(page, "og:url");
  expect(ogUrl, "og:url must be present").toBeTruthy();
  expect(new URL(ogUrl!).origin).toBe(origin);

  const ogImage = await ogTag(page, "og:image");
  expect(ogImage, "og:image must be present").toBeTruthy();
  expect(new URL(ogImage!).origin).toBe(origin);
  expect(new URL(ogImage!).pathname).toBe(OG_IMAGE_PATH);
  // Dimensions let unfurlers reserve layout before the bytes land.
  expect(await ogTag(page, "og:image:width")).toBe("1200");
  expect(await ogTag(page, "og:image:height")).toBe("630");

  // Twitter/X. Anything other than summary_large_image renders the
  // thumbnail as a small square instead of a full-bleed card.
  expect(await nameTag(page, "twitter:card")).toBe("summary_large_image");
  expect((await nameTag(page, "twitter:title"))?.trim()).toBeTruthy();
  expect((await nameTag(page, "twitter:description"))?.trim()).toBeTruthy();
  const twitterImage = await nameTag(page, "twitter:image");
  expect(twitterImage, "twitter:image must be present").toBeTruthy();
  expect(new URL(twitterImage!).origin).toBe(origin);
});

test("the og:image resolves to an absolute URL that returns 200", async ({
  page,
  request,
}) => {
  await page.goto("/", { waitUntil: "domcontentloaded" });
  const ogImage = await ogTag(page, "og:image");
  expect(ogImage).toBeTruthy();

  // Fetch the absolute URL exactly as an unfurler would — no baseURL
  // resolution, so a broken origin fails here rather than passing by
  // accident.
  const res = await request.get(new URL(ogImage!).toString());
  expect(res.status(), `${ogImage} should return 200`).toBe(200);
  expect(res.headers()["content-type"]).toContain("image/");
  // A card that is a few hundred bytes is a placeholder, not an image.
  expect((await res.body()).length).toBeGreaterThan(5_000);
});

test("every public route canonicalises to itself, not to the home page", async ({
  page,
}) => {
  for (const route of ROUTES) {
    await page.goto(route, { waitUntil: "domcontentloaded" });
    const origin = new URL(page.url()).origin;

    const canonical = await page
      .locator('head link[rel="canonical"]')
      .first()
      .getAttribute("href");
    expect(canonical, `${route} should emit <link rel=canonical>`).toBeTruthy();

    const parsed = new URL(canonical!);
    expect(parsed.origin, `${route} canonical must be absolute`).toBe(origin);
    expect(
      normalizePath(parsed.pathname),
      `${route} canonical points at ${parsed.pathname} — it inherited the ` +
        `root layout's canonical instead of declaring its own`,
    ).toBe(route);

    // og:url has to agree with the canonical, or the unfurled preview
    // and the indexed page disagree about which URL this is.
    const ogUrl = await ogTag(page, "og:url");
    expect(ogUrl, `${route} should emit og:url`).toBeTruthy();
    expect(normalizePath(new URL(ogUrl!).pathname)).toBe(route);

    // Social block travels with every page, not just the home page.
    expect(
      await nameTag(page, "twitter:card"),
      `${route} should emit twitter:card`,
    ).toBe("summary_large_image");
    const ogImage = await ogTag(page, "og:image");
    expect(ogImage, `${route} should emit og:image`).toBeTruthy();
    expect(new URL(ogImage!).pathname).toBe(OG_IMAGE_PATH);
  }
});
