// Functional: ABS-325 — the off-menu "Other" free-form question option is
// disabled at launch.
//
// The off-menu path (ABS-316) let a user type a free-form question, had the
// LLM quote a price, and sold an answer. It is too open-ended to expose at
// launch (unbounded scope / quoting / liability), so the entry point is
// removed from the case-open form and the backend quote / checkout-other
// endpoints are gated behind ADVISOR_OTHER_QUESTION_ENABLED (default false).
// The quote/answer engine is left intact — re-enabling is a flag flip.
//
// This spec pins the absence of the UI entry point: the question menu
// offers only the fixed catalog questions, with no "Other" card and no
// free-form quote UI reachable from it.

import { expect, test } from "../fixtures/test-env";

test("the question menu offers no off-menu 'Other' option", async ({
  page,
}) => {
  await page.goto("/cases/new");
  await expect(page.getByTestId("question-menu")).toBeVisible();

  // The fixed catalog questions are still offered.
  await expect(
    page.getByTestId("question-option-permitted_use"),
  ).toBeVisible();

  // The off-menu "Other" entry point is gone.
  await expect(page.getByTestId("question-option-other")).toHaveCount(0);
  await expect(
    page.getByTestId("question-option-other-quote"),
  ).toHaveCount(0);

  // None of the off-menu free-form copy is rendered anywhere on the page.
  const body = (await page.textContent("body")) ?? "";
  expect(body).not.toContain("my question isn't listed");
  expect(body).not.toContain("Get a price (free)");
  expect(body).not.toContain("Describe the off-menu question");
});

test("no free-form quote UI is reachable after picking a catalog question", async ({
  page,
}) => {
  await page.goto("/cases/new");
  await expect(page.getByTestId("question-menu")).toBeVisible();

  // Selecting a catalog question shows the consultant-style intake, never
  // the off-menu quote panel.
  await page.getByTestId("question-option-permitted_use").click();
  await expect(page.getByText(/Describe your situation/i)).toBeVisible();

  await expect(page.getByTestId("other-quote")).toHaveCount(0);
  await expect(page.getByTestId("free-trial-btn-other")).toHaveCount(0);
  await expect(
    page.getByRole("button", { name: /Get a price \(free\)/i }),
  ).toHaveCount(0);
});
