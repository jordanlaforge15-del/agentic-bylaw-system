// Functional: zero-reports posture across the pricing + conversation surfaces.
//
// /pricing (ABS-387, shared with I6): when no report slug is enabled
// (ADVISOR_ENABLED_QUESTIONS unset/empty), the pricing page must still read as
// a complete conversation-SKU page — the "Written reports" section is absent
// entirely, and the inverted "NEED A WRITTEN REPORT? Get in touch." contact
// card takes the jurisdiction FAQ's slot, pointing at the real support address.
//
// /cases/new (ABS-385, shared with I8/ABS-387): released report SKUs
// (per-report gates, ABS-384) are a SECONDARY accordion. The gate is
// deny-by-default: with zero enabled slugs the page must read complete as
// anchor + question + free CTA, with NO report section, heading, or count
// anywhere. With ≥1 enabled slug the "OR ORDER A WRITTEN REPORT · N" accordion
// renders for the enabled slugs only, nothing pre-selected, and a
// `?report=<slug>` deep-link auto-expands that offer.
//
// The e2e stack's server env enables all five slugs for the whole run, so the
// empty-gate cases stub the proxied menu response (each page fetches
// /api/billing/questions client-side). Proves each surface faithfully renders
// whatever subset the gate advertises.

import { expect, test } from "../fixtures/test-env";

async function stubEmptyMenu(page: import("@playwright/test").Page) {
  await page.route("**/api/billing/questions", async (route) => {
    // The zero-report assertions all check for *absence*, so they resolve
    // before this real round-trip completes and the page can close mid-fetch.
    // Swallow the resulting "page closed" rejection instead of failing.
    try {
      const resp = await route.fetch();
      const body = await resp.json();
      body.questions = [];
      await route.fulfill({ response: resp, json: body });
    } catch {
      /* page closed before the menu fetch settled — nothing to fulfill */
    }
  });
}

test("zero enabled reports: no reports section, contact card present", async ({
  page,
}) => {
  await stubEmptyMenu(page);
  await page.goto("/pricing");

  // The conversation SKU (trial + top-ups) still renders — the page is
  // complete without any reports.
  await expect(page.getByTestId("trial-card")).toBeVisible();
  await expect(page.getByTestId("topup-card-small")).toBeVisible();

  // No written-reports section at all (the "WRITTEN REPORTS · N AVAILABLE"
  // section heading is absent — the contact card's prose copy may still
  // mention "written reports", so match the heading shape specifically).
  await expect(page.getByTestId("reports-section")).toHaveCount(0);
  await expect(page.locator("body")).not.toContainText(/WRITTEN REPORTS ·/i);
  for (const slug of [
    "permitted_use",
    "development_standards",
    "due_diligence",
    "legal_nonconforming",
    "variance_justification",
  ]) {
    await expect(page.getByTestId(`report-sku-${slug}`)).toHaveCount(0);
  }

  // The inverted contact card replaces the jurisdiction FAQ, with a real
  // support address.
  const contact = page.getByTestId("report-contact-card");
  await expect(contact).toContainText("NEED A WRITTEN REPORT?");
  await expect(contact).toContainText(/Get in touch/i);
  await expect(contact.locator('a[href^="mailto:"]')).toHaveCount(1);

  // The turn FAQ is intact.
  const faq = page.getByTestId("pricing-faq");
  await expect(faq).toContainText("What's a turn?");
});

test("zero enabled slugs: no report section renders, page still reads complete", async ({
  page,
}) => {
  await stubEmptyMenu(page);

  await page.goto("/cases/new");

  // The core conversation surface is present and usable.
  await expect(page.getByPlaceholder(/1234 Main St, Halifax/)).toBeVisible();
  await expect(page.getByPlaceholder(/Ask your question/)).toBeVisible();
  await expect(page.getByTestId("start-conversation-btn")).toBeVisible();

  // No report section, heading, count, or accordion container.
  await expect(page.getByTestId("question-menu")).toHaveCount(0);
  await expect(page.getByText(/OR ORDER A WRITTEN REPORT/)).toHaveCount(0);
});

test("≥1 enabled slug: the report accordion renders enabled slugs, nothing pre-selected", async ({
  page,
}) => {
  // The e2e stack enables all five launch slugs (ADVISOR_ENABLED_QUESTIONS='*').
  await page.goto("/cases/new");

  const menu = page.getByTestId("question-menu");
  await expect(menu).toBeVisible();
  await expect(page.getByText(/OR ORDER A WRITTEN REPORT · 5/)).toBeVisible();

  // Nothing pre-selected: the intake composer is not shown until an offer is
  // expanded.
  await expect(page.getByPlaceholder(/Tell us what you want answered/i)).toHaveCount(
    0,
  );

  // Clicking an offer expands it (reveals the intake composer).
  await page.getByTestId("question-option-permitted_use").click();
  await expect(
    page.getByPlaceholder(/Tell us what you want answered/i),
  ).toBeVisible();
});

test("?report=<slug> deep-link auto-expands that offer", async ({ page }) => {
  await page.goto("/cases/new?report=due_diligence");

  await expect(page.getByTestId("question-menu")).toBeVisible();
  // The due_diligence offer is expanded on load; its intake composer shows
  // without any click.
  await expect(
    page.getByPlaceholder(/Tell us what you want answered/i),
  ).toBeVisible();
  await expect(
    page.getByTestId("question-option-due_diligence"),
  ).toHaveAttribute("aria-expanded", "true");
});
