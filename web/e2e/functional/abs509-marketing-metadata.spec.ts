// Functional: every public marketing route emits its own <title> and
// <meta name="description"> (ABS-509).
//
// Before this, no marketing page.tsx exported `metadata`, so all of them
// inherited the root layout's single title/description and read as
// duplicates to a search engine. The guard here is deliberately structural
// rather than copy-exact: it walks the public routes, collects the two tags,
// and asserts they are present, brand-stamped, length-sane, and — the part
// that actually catches a regression — pairwise unique across the site.
//
// Adding a marketing route? Add it to ROUTES. A new page that forgets its
// metadata export will inherit the root title and fail the uniqueness check.

import { expect, test } from "../fixtures/test-env";

const ROUTES = [
  "/",
  "/pricing",
  "/coverage",
  "/about",
  "/support",
  "/changelog",
  "/privacy",
  "/terms",
  "/signup",
] as const;

// Google truncates around here; the ticket caps descriptions at ~160.
const MAX_DESCRIPTION_LENGTH = 160;

test("every public marketing route has a unique title and description", async ({
  page,
}) => {
  const titles = new Map<string, string>();
  const descriptions = new Map<string, string>();

  for (const route of ROUTES) {
    await page.goto(route, { waitUntil: "domcontentloaded" });

    const title = (await page.title()).trim();
    expect(title, `${route} should have a non-empty <title>`).not.toBe("");
    // Brand stays in the SERP on every page.
    expect(title, `${route} title should carry the brand`).toContain("ABS°");

    const description = (
      (await page
        .locator('head meta[name="description"]')
        .first()
        .getAttribute("content")) ?? ""
    ).trim();
    expect(
      description,
      `${route} should have a non-empty meta description`,
    ).not.toBe("");
    expect(
      description.length,
      `${route} description is ${description.length} chars: "${description}"`,
    ).toBeLessThanOrEqual(MAX_DESCRIPTION_LENGTH);

    titles.set(route, title);
    descriptions.set(route, description);
  }

  // Uniqueness: report the offending routes, not just a count mismatch.
  expectNoDuplicates(titles, "title");
  expectNoDuplicates(descriptions, "description");
});

function expectNoDuplicates(byRoute: Map<string, string>, label: string) {
  const seen = new Map<string, string>();
  const collisions: string[] = [];
  for (const [route, value] of byRoute) {
    const previous = seen.get(value);
    if (previous !== undefined) {
      collisions.push(`${previous} and ${route} share the ${label} "${value}"`);
    } else {
      seen.set(value, route);
    }
  }
  expect(collisions, collisions.join("; ")).toEqual([]);
}
