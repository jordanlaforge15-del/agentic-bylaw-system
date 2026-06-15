// Functional: GET /api/billing/questions serves the priced-question
// launch menu with the decision-grounded amounts.
//
// ABS-311 introduces the "buy an answer" launch product: a fixed menu
// of 5 priced questions, one Stripe Price each (no packs, no credit
// ledger). The menu is served by the backend at
// /v1/billing/questions and proxied through Next at
// /api/billing/questions for the (public) question-menu page.
//
// This spec exercises the Next-proxy -> FastAPI path and locks the
// launch prices (CAD cents) so a catalog edit can't silently drift the
// menu away from docs/decisions/2026-06-priced-question-catalog.md.

import { expect, test } from "../fixtures/test-env";

// slug -> price in CAD cents, from the decision doc.
const EXPECTED_PRICES: Record<string, number> = {
  permitted_use: 7_900,
  development_standards: 14_900,
  due_diligence: 19_900,
  legal_nonconforming: 19_900,
  variance_justification: 29_900,
};

test("question menu serves the 5 launch questions with grounded prices", async ({
  page,
}) => {
  const res = await page.request.get("/api/billing/questions");
  expect(res.ok()).toBeTruthy();

  const body = await res.json();
  expect(body.currency).toBe("CAD");
  expect(Array.isArray(body.questions)).toBeTruthy();
  expect(body.questions).toHaveLength(5);

  // Menu order is cheapest -> most expensive.
  const slugs = body.questions.map((q: { slug: string }) => q.slug);
  expect(slugs).toEqual([
    "permitted_use",
    "development_standards",
    "due_diligence",
    "legal_nonconforming",
    "variance_justification",
  ]);

  for (const q of body.questions) {
    expect(q.price_cents).toBe(EXPECTED_PRICES[q.slug]);
    expect(q.currency).toBe("CAD");
    // Every question carries its engine binding + input schema so the
    // buy-an-answer flow knows what to collect and how to ask.
    expect(q.backing_calls.length).toBeGreaterThan(0);
    expect(q.required_inputs.length).toBeGreaterThan(0);
    const inputNames = q.required_inputs.map(
      (f: { name: string }) => f.name,
    );
    expect(inputNames).toContain("address");
  }

  // Billing is dormant in the e2e stack (no Stripe configured), so every
  // question is rendered but not yet purchasable.
  expect(body.enabled).toBe(false);
  for (const q of body.questions) {
    expect(q.available).toBe(false);
  }
});
