// Functional: /pricing renders the priced-question menu with grounded amounts.
//
// ABS-310 replaces the turn-pack tier matrix with a per-question menu.
// The five launch questions have fixed CAD prices grounded in the
// decision doc (docs/decisions/2026-06-priced-question-catalog.md).
// Asserting the rendered amounts locks the catalog wiring — a price
// change in the backend code causes this spec to fail.

import { expect, test } from "../fixtures/test-env";

test("pricing page shows question cards with grounded CAD amounts", async ({
  page,
}) => {
  await page.goto("/pricing");

  // Five question cards — one per catalog question. Each card carries a
  // data-testid="question-card-{slug}" so the assertion is scoped to the
  // card rather than relying on DOM hierarchy.
  const permitted = page.getByTestId("question-card-permitted_use");
  const devStd = page.getByTestId("question-card-development_standards");
  const dueDil = page.getByTestId("question-card-due_diligence");
  const legalNc = page.getByTestId("question-card-legal_nonconforming");
  const variance = page.getByTestId("question-card-variance_justification");

  await expect(permitted).toContainText("$79");
  await expect(devStd).toContainText("$149");
  await expect(dueDil).toContainText("$199");
  await expect(legalNc).toContainText("$199");
  await expect(variance).toContainText("$299");
});
