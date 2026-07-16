// Functional: /pricing uses the "Pay by the turn" copy, not the retired
// question-menu or turn-pack tier copy (ABS-387).
//
// ABS-387 pivots the pricing page from the per-question menu (ABS-310) to the
// conversation SKU: a turns kicker, a "Pay by the turn." hero, and turn-based
// FAQ. This spec confirms the new copy is present and the stale surfaces
// (question kicker, tier/credit vocabulary, per-answer pricing) are gone.

import { expect, test } from "../fixtures/test-env";

test("pricing page shows pay-by-the-turn copy, not the retired menus", async ({
  page,
}) => {
  await page.goto("/pricing");

  // New surface: turns kicker + hero + turn FAQ.
  await expect(page.locator("body")).toContainText("PRICING · TURNS");
  await expect(
    page.getByRole("heading", { level: 1, name: /Pay by the turn/i }),
  ).toBeVisible();
  const faq = page.getByTestId("pricing-faq");
  await expect(faq).toContainText("What's a turn?");
  await expect(faq).toContainText("Why is my turn count approximate?");
  await expect(faq).toContainText("What happens when I run out?");

  // The retired question-menu + tier/credit copy must be absent.
  const text = (await page.textContent("body")) ?? "";
  expect(text).not.toContain("PRICING · QUESTIONS");
  expect(text).not.toContain("Pick your question");
  expect(text).not.toContain("per answer");
  expect(text).not.toContain("reasoning steps");
  expect(text).not.toContain("retrieval rounds");
  expect(text).not.toContain("CASE CREDITS");
  expect(text).not.toContain("quick credit");
  expect(text).not.toContain("standard credit");
  expect(text).not.toContain("complex credit");
});
