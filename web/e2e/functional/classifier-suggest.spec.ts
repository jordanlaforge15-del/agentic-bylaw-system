// Functional: the pre-flight classifier banner reflects the
// dispatcher's recommendation. The mock dispatcher
// (src/advisor/llm/mock_dispatcher.py) inspects the anchor + message
// for keywords:
//   * "MOCK_QUICK" / "simple"   → quick, 92%
//   * "MOCK_COMPLEX" / "rezoning" → complex, 90%
//   * default                    → standard, 85%
//
// Specs assert the banner reflects the choice. This also serves as a
// smoke-style check that classifier wiring goes through the e2e_server
// (not the production gateway which would need an API key).

import { expect, test } from "../fixtures/test-env";

test.describe("classifier banner", () => {
  test("returns quick for a 'simple' inquiry", async ({ page }) => {
    await page.goto("/cases/new");
    await page
      .getByPlaceholder(/1234 Main St, Halifax/)
      .fill(`simple-${Date.now()}`);
    await page
      .getByPlaceholder(/Describe the inquiry/)
      .fill("simple zoning lookup, just the front yard setback");
    await page
      .getByRole("button", { name: /Get tier recommendation/ })
      .click();
    await expect(page.getByText(/CLASSIFIER RECOMMENDS · 92%/)).toBeVisible();
    // Two "Quick Lookup" texts: the classifier banner and the tier
    // radio label. The first one in DOM order is the banner — scope
    // to it so changing the radio labels later doesn't break this.
    await expect(page.getByText(/Quick Lookup/).first()).toBeVisible();
  });

  test("returns complex for a 'rezoning' inquiry", async ({ page }) => {
    await page.goto("/cases/new");
    await page
      .getByPlaceholder(/1234 Main St, Halifax/)
      .fill(`complex-${Date.now()}`);
    await page
      .getByPlaceholder(/Describe the inquiry/)
      .fill("considering a rezoning with heritage overlay implications");
    await page
      .getByRole("button", { name: /Get tier recommendation/ })
      .click();
    await expect(page.getByText(/CLASSIFIER RECOMMENDS · 90%/)).toBeVisible();
    await expect(page.getByText(/Complex File/).first()).toBeVisible();
  });
});
