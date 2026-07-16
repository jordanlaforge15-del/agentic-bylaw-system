// Functional: /pricing "Written reports" section reflects the report gate
// with grounded amounts (ABS-387).
//
// The pricing page's secondary section lists the enabled written-report SKUs
// (per-report gate, ABS-384) with their fixed CAD prices grounded in the
// decision doc (docs/decisions/2026-06-priced-question-catalog.md). The e2e
// stack enables all five report slugs (ADVISOR_ENABLED_QUESTIONS='*'), so the
// section advertises `WRITTEN REPORTS · 5 AVAILABLE` and one ReportSku card
// per slug. A price drift in the backend catalog fails this spec.
//
// The zero / one gate subsets (section absent / single card) are covered in
// report-gate-matrix.spec.ts and zero-reports-posture.spec.ts, which stub the
// proxied menu — the server env is fixed to '*' for the whole run.

import { expect, test } from "../fixtures/test-env";

// slug -> price in CAD dollars, from the decision doc.
const EXPECTED: Array<{ slug: string; dollars: number }> = [
  { slug: "permitted_use", dollars: 79 },
  { slug: "development_standards", dollars: 149 },
  { slug: "due_diligence", dollars: 199 },
  { slug: "legal_nonconforming", dollars: 199 },
  { slug: "variance_justification", dollars: 299 },
];

test("pricing page lists enabled report SKUs with grounded amounts", async ({
  page,
}) => {
  await page.goto("/pricing");

  // All five slugs enabled → the reports section renders with the count.
  await expect(page.getByTestId("reports-section")).toContainText(
    /WRITTEN REPORTS · 5 AVAILABLE/i,
  );

  for (const { slug, dollars } of EXPECTED) {
    const card = page.getByTestId(`report-sku-${slug}`);
    await expect(card).toBeVisible();
    await expect(card).toContainText(`$${dollars}`);
    await expect(card).toContainText(/FIXED-PRICE REPORT · ONE-TIME/i);
    await expect(card).toContainText(/Order report/i);
  }
});
