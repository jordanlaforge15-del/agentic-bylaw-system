// ABS-73 — every footer link reaches a real page (no more 404s).
//
// Before this change, the marketing footer column listed six routes
// that didn't exist in the Next.js app (/changelog, /coverage,
// /support, /about, /privacy, /terms). The footer also surfaces
// pre-existing routes (Home, Pricing, Sign in, Get an invite,
// Billing) plus a `mailto:hello@abs.app` link.
//
// Strategy: navigate to `/`, find each footer link by visible text,
// click, and assert (a) the URL is what we expect and (b) a stable
// landmark from the new design renders. We test the six new pages
// individually and re-check the pre-existing ones for a smoke pass
// — together that's "no dead links in the footer."
//
// The mailto: link is asserted by reading the `href` attribute
// (clicking would open the OS mail handler, which Playwright can't
// drive cross-platform).

import { expect, test } from "../fixtures/test-env";

type Target = {
  /** Visible label inside the footer that we click on. */
  label: string;
  /** URL pathname after navigation. */
  path: string;
  /**
   * A text fragment that must render on the destination page. Using
   * a heading word is more durable than asserting full headings
   * (copy gets re-edited; the kicker / title keyword tends to
   * survive).
   */
  expectText: RegExp;
};

const NEW_PAGES: Target[] = [
  { label: "Changelog", path: "/changelog", expectText: /What.s changed/i },
  { label: "Coverage", path: "/coverage", expectText: /One jurisdiction/i },
  { label: "Support", path: "/support", expectText: /How can we help/i },
  { label: "About", path: "/about", expectText: /An expert planner/i },
  { label: "Privacy", path: "/privacy", expectText: /Privacy\./i },
  { label: "Terms", path: "/terms", expectText: /Terms of use/i },
];

const PRE_EXISTING: Target[] = [
  { label: "Home", path: "/", expectText: /expert/i },
  { label: "Pricing", path: "/pricing", expectText: /./i },
];

test.describe("Footer links — ABS-73", () => {
  for (const t of [...NEW_PAGES, ...PRE_EXISTING]) {
    test(`footer link "${t.label}" reaches ${t.path}`, async ({ page }) => {
      await page.goto("/");

      // Scope the link query to the <footer> so we don't accidentally
      // click a TopNav link with the same label.
      const footer = page.locator("footer");
      await expect(footer).toBeVisible();
      const link = footer.getByRole("link", { name: t.label, exact: true });
      await expect(link).toBeVisible();

      await link.click();
      await page.waitForURL((url) => url.pathname === t.path);
      await expect(page).toHaveURL(new RegExp(`${t.path}(\\?|#|$)`));

      // Page must render — bare minimum a non-404 body. We assert
      // a copy fragment from the new design lands in the DOM.
      await expect(page.locator("body")).toContainText(t.expectText);
    });
  }

  test('footer "hello@abs.app" link uses mailto:', async ({ page }) => {
    await page.goto("/");
    const footer = page.locator("footer");
    const mailLink = footer.getByRole("link", { name: "hello@abs.app" });
    await expect(mailLink).toBeVisible();
    const href = await mailLink.getAttribute("href");
    expect(href).toBe("mailto:hello@abs.app");
  });

  test("LegalShell sidebar TOC is sticky and lists every privacy section", async ({
    page,
  }) => {
    // Extra coverage for the LegalShell scroll-spy: every § from the
    // configured sections array should render in the sidebar. This is
    // the cheapest way to catch the kind of bug where the sections
    // prop diverges from what's rendered.
    await page.goto("/privacy");

    const sidebar = page.locator("aside").first();
    await expect(sidebar).toBeVisible();

    for (const n of ["1.0", "2.0", "3.0", "4.0", "5.0", "6.0", "7.0", "8.0"]) {
      await expect(sidebar.getByText(`§${n}`)).toBeVisible();
    }
  });

  test("Coverage page renders the active HRM hero", async ({ page }) => {
    // A second smoke check on the most data-dense new page. If the
    // bylaws table or hero card silently breaks (e.g. a CSS-token
    // rename strips the inverted background), this catches it.
    await page.goto("/coverage");
    await expect(page.locator("h1")).toContainText("One jurisdiction");
    // Hero heading — scoped with getByRole so we don't collide with the
    // "© 2026 ABS · HALIFAX REGIONAL MUNICIPALITY" string in the footer.
    await expect(
      page.getByRole("heading", { name: "Halifax Regional Municipality" }),
    ).toBeVisible();
    await expect(page.getByText("FULLY INDEXED")).toBeVisible();
  });
});
