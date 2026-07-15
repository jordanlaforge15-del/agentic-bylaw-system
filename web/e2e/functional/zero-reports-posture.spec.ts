// Functional: zero-reports posture on /pricing (ABS-387, shared with I6).
//
// When no report slug is enabled (ADVISOR_ENABLED_QUESTIONS unset/empty),
// the pricing page must still read as a complete conversation-SKU page: the
// "Written reports" section is absent entirely, and the inverted
// "NEED A WRITTEN REPORT? Get in touch." contact card takes the jurisdiction
// FAQ's slot — pointing at the real support address, not a placeholder.
//
// The e2e stack's server env enables all five slugs for the whole run, so
// this drives the empty-gate subset by stubbing the proxied menu response
// (the page fetches /api/billing/questions client-side). Proves the pricing
// page faithfully renders whatever subset the gate advertises.

import { expect, test } from "../fixtures/test-env";

async function stubEmptyMenu(page: import("@playwright/test").Page) {
  await page.route("**/api/billing/questions", async (route) => {
    const resp = await route.fetch();
    const body = await resp.json();
    body.questions = [];
    await route.fulfill({ response: resp, json: body });
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

  // No written-reports section at all.
  await expect(page.getByTestId("reports-section")).toHaveCount(0);
  await expect(page.locator("body")).not.toContainText(/WRITTEN REPORTS/i);
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
