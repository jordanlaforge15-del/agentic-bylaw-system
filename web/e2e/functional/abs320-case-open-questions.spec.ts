// Functional: ABS-320 — /cases/new sells PRICED QUESTIONS, not tiers.
//
// The product pivoted from the quick/standard/complex tier model to a
// priced-question catalog ("buy an answer"). This spec pins the migrated
// primary entry flow: the case-open form renders the live question menu
// (GET /api/billing/questions — the 5 launch questions + an "Other"
// option), keeps the free anchor input + in-window match behaviour, and
// shows none of the retired tier / reasoning-step / classifier copy.
//
// Billing is dormant in the e2e stack (no Stripe), so the catalog marks
// every question `available: false`. The form therefore renders the menu
// and prices but degrades checkout to a "not configured yet" notice —
// mirroring the pricing page + buy-question-button. (The intake -> checkout
// and quote -> buy money paths are exercised against the real services by
// abs315 / abs316 via the /v1/_test/buy-answer harness; here we pin the UI
// wiring and the cross-surface removal of the tier model.)

import { expect, openCaseViaApi, test } from "../fixtures/test-env";

test("case-open form renders the priced-question menu, not tiers", async ({
  page,
}) => {
  await page.goto("/cases/new");

  // The question menu loads from the live catalog.
  await expect(page.getByTestId("question-menu")).toBeVisible();

  // The 5 launch questions are present, with grounded prices, plus "Other".
  await expect(
    page.getByTestId("question-option-permitted_use"),
  ).toBeVisible();
  await expect(
    page.getByTestId("question-option-development_standards"),
  ).toBeVisible();
  await expect(
    page.getByTestId("question-option-due_diligence"),
  ).toBeVisible();
  await expect(
    page.getByTestId("question-option-legal_nonconforming"),
  ).toBeVisible();
  await expect(
    page.getByTestId("question-option-variance_justification"),
  ).toBeVisible();
  await expect(page.getByTestId("question-option-other")).toBeVisible();

  // The cheapest launch question shows its $79 anchor price.
  await expect(
    page.getByTestId("question-option-permitted_use"),
  ).toContainText("$79");

  // The retired tier / classifier / reasoning-step model is gone.
  const body = await page.textContent("body");
  if (body) {
    expect(body).not.toContain("Get tier recommendation");
    expect(body).not.toContain("reasoning steps");
    expect(body).not.toContain("Open at tier");
    expect(body).not.toContain("CLASSIFIER RECOMMENDS");
  }
});

test("selecting a question opens its intake; dormant billing degrades checkout", async ({
  page,
}) => {
  await page.goto("/cases/new");
  await expect(page.getByTestId("question-menu")).toBeVisible();

  // Selecting a catalog question reveals the consultant-style intake box.
  await page.getByTestId("question-option-permitted_use").click();
  await expect(page.getByText(/Describe your situation/i)).toBeVisible();

  // With billing dormant (no Stripe) the question is not purchasable, so
  // the form surfaces the "not configured" notice instead of a buy button.
  await expect(
    page.getByText(/Checkout isn.?t configured for this question yet/i),
  ).toBeVisible();
});

test("the Other path offers a free-quote flow", async ({ page }) => {
  await page.goto("/cases/new");
  await expect(page.getByTestId("question-menu")).toBeVisible();

  await page.getByTestId("question-option-other").click();
  await expect(page.getByText(/Describe your question/i)).toBeVisible();

  // Dormant billing also gates the off-menu buy path.
  await expect(
    page.getByText(/Off-menu checkout isn.?t configured yet/i),
  ).toBeVisible();
});

test("anchor still surfaces an in-window existing-case match", async ({
  page,
}) => {
  // A case is a free container — opening/continuing one never charges. The
  // match lookup that powers "continue without buying again" is preserved.
  const anchor = `abs320-match-${Date.now()}`;
  await openCaseViaApi({ anchorLabel: anchor });

  await page.goto("/cases/new");
  const anchorInput = page.getByPlaceholder(/1234 Main St, Halifax/);
  await anchorInput.fill(anchor);
  await anchorInput.blur();

  await expect(page.getByText(/EXISTING CASE FOUND/)).toBeVisible();
  await expect(
    page.getByRole("button", { name: /Continue case/ }),
  ).toBeVisible();
});
