// ABS-73 wired the six footer pages (no more 404s). ABS-78 then
// fact-checked the copy so the pages reflect the real state of the
// app — single bylaw indexed, real legal entity, real contact email,
// no fake team / changelog / status panel / chat. This spec covers
// both: the footer links don't 404, AND the destination copy matches
// what the app is actually claiming about itself.
//
// Strategy: navigate to `/`, find each footer link by visible text,
// click, and assert (a) the URL is what we expect and (b) a stable
// copy fragment from the post-ABS-78 page renders.
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
   * A text fragment that must render on the destination page. We pick
   * fragments tied to claims we want to *keep* true (the bylaw name,
   * the entity name, the beta status) rather than incidental headings
   * — so the test fails if someone re-introduces placeholder copy.
   */
  expectText: RegExp;
};

const NEW_PAGES: Target[] = [
  { label: "Changelog", path: "/changelog", expectText: /PRIVATE BETA/i },
  {
    label: "Coverage",
    path: "/coverage",
    expectText: /Regional Centre Land Use By-law/i,
  },
  { label: "Support", path: "/support", expectText: /How can we help/i },
  { label: "About", path: "/about", expectText: /An expert planner/i },
  {
    label: "Privacy",
    path: "/privacy",
    expectText: /Agentic Bylaw Systems/i,
  },
  { label: "Terms", path: "/terms", expectText: /Terms of use/i },
];

const PRE_EXISTING: Target[] = [
  { label: "Home", path: "/", expectText: /expert/i },
  { label: "Pricing", path: "/pricing", expectText: /./i },
];

const SUPPORT_EMAIL = "info@agenticbylawsystems.com";

test.describe("Footer links — ABS-73 / ABS-78", () => {
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
      // a copy fragment we want to *keep* true lands in the DOM.
      await expect(page.locator("body")).toContainText(t.expectText);
    });
  }

  test(`footer "${SUPPORT_EMAIL}" link uses mailto:`, async ({ page }) => {
    // ABS-78 replaced the made-up hello@abs.app handle with the real
    // info@agenticbylawsystems.com address used in the Terms doc.
    await page.goto("/");
    const footer = page.locator("footer");
    const mailLink = footer.getByRole("link", { name: SUPPORT_EMAIL });
    await expect(mailLink).toBeVisible();
    const href = await mailLink.getAttribute("href");
    expect(href).toBe(`mailto:${SUPPORT_EMAIL}`);
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

  test("Coverage page advertises only the Regional Centre LUB", async ({
    page,
  }) => {
    // Post-ABS-78: the bylaws table is collapsed to the single
    // document we actually index. This guards against a regression
    // that re-introduces the placeholder six-bylaw inventory.
    await page.goto("/coverage");
    await expect(page.locator("h1")).toContainText("One jurisdiction");
    // Hero heading — scoped with getByRole so we don't collide with the
    // "© 2026 ABS · HALIFAX REGIONAL MUNICIPALITY" string in the footer.
    await expect(
      page.getByRole("heading", { name: "Halifax Regional Centre" }),
    ).toBeVisible();
    await expect(page.getByText("PRIMARY BYLAW INDEXED")).toBeVisible();
    // The placeholder doc names that ABS-78 removed must stay removed.
    await expect(page.locator("body")).not.toContainText(
      /Land Use By-law for Dartmouth/i,
    );
    await expect(page.locator("body")).not.toContainText(
      /Centre Plan — Package A/i,
    );
  });

  test("Privacy page names the real entity and avoids tech-stack brands", async ({
    page,
  }) => {
    // Guard against re-introducing the fictional "ABS Reading Inc."
    // entity, and against re-introducing infrastructure brand names
    // (Hetzner, Postgres) that the policy deliberately doesn't disclose.
    await page.goto("/privacy");
    await expect(page.locator("body")).toContainText(/Agentic Bylaw Systems/i);
    await expect(page.locator("body")).not.toContainText(/ABS Reading Inc\./i);
    await expect(page.locator("body")).not.toContainText(/Hetzner/i);
    await expect(page.locator("body")).not.toContainText(/Postgres/i);
  });

  test("Support page does not advertise channels that don't exist", async ({
    page,
  }) => {
    // ABS-78 removed the fake live-chat / office-hours / status-dot
    // panels. The page should only surface the two channels we
    // actually monitor.
    await page.goto("/support");
    await expect(page.locator("body")).toContainText(SUPPORT_EMAIL);
    await expect(page.locator("body")).not.toContainText(/Thursdays, 11am/i);
    await expect(page.locator("body")).not.toContainText(/In-app chat/i);
    await expect(page.locator("body")).not.toContainText(/247 ms/i);
  });
});
