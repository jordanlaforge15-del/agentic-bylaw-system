// Functional: /pricing renders the halved tier prices.
//
// ABS-24 cut every tier's unit price in half. The pricing page is
// fully dynamic — it fetches the backend catalog and formats each
// offer with Intl.NumberFormat("en-CA", "CAD"). Asserting the three
// PAYG amounts is enough to catch regressions in the catalog wiring:
// PAYG has no discount, so the rendered figure equals the tier's
// unit price.

import { expect, test } from "../fixtures/test-env";

test("pricing page shows halved PAYG prices for each tier", async ({
  page,
}) => {
  await page.goto("/pricing");

  // Each tier section renders a Pay-as-you-go card with the unit
  // price as its headline. Scope the assertion to the card that
  // identifies the matching credit ("1 quick credit" / "1 standard
  // credit" / "1 complex credit") so the test isn't fooled by the
  // multi-credit pack cards.
  const quickPayg = page
    .locator("div")
    .filter({ hasText: /^1 quick credit$/ })
    .locator("..");
  const standardPayg = page
    .locator("div")
    .filter({ hasText: /^1 standard credit$/ })
    .locator("..");
  const complexPayg = page
    .locator("div")
    .filter({ hasText: /^1 complex credit$/ })
    .locator("..");

  await expect(quickPayg).toContainText("$12.50");
  await expect(standardPayg).toContainText("$32.50");
  await expect(complexPayg).toContainText("$75");
});
