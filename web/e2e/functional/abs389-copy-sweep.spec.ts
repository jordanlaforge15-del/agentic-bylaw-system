// Functional: ABS-389 — marketing/nav copy sweep to the conversation +
// turns product language. This is the single "sweep" spec the ticket calls
// for: it pins the key labels on the three primary surfaces (workspace
// account menu, /app sidebar CTA + empty state, and the marketing home
// hero) so a regression back to the retired "Open a case" / per-answer /
// tier vocabulary fails loudly. Surface-specific assertions also live in
// their owning specs (abs334 for the menu/sidebar); this guards them as a
// set.

import { expect, test } from "../fixtures/test-env";

test("workspace menu speaks conversation + turns, not 'Open a case'", async ({
  page,
}) => {
  await page.goto("/app");
  await page.getByRole("button", { name: "Workspace menu" }).click();
  const menu = page.getByRole("menu");
  await expect(menu).toBeVisible();

  // New-conversation CTA replaced the retired "Open a case" label.
  await expect(
    menu.getByRole("menuitem", { name: /New conversation/ }),
  ).toBeVisible();
  await expect(
    menu.getByRole("menuitem", { name: /Open a case/ }),
  ).toHaveCount(0);

  // Billing hint speaks the turns wallet.
  await expect(menu.getByText("Turn balance & top-ups")).toBeVisible();
});

test("sidebar CTA + empty state are conversation-first", async ({ page }) => {
  await page.goto("/app");

  // Primary action is the new-conversation CTA, not "+ Open a case".
  await expect(
    page.getByRole("button", { name: "+ New conversation" }),
  ).toBeVisible();
  await expect(
    page.getByRole("button", { name: "+ Open a case" }),
  ).toHaveCount(0);
});

test("home hero uses conversation framing, no per-answer copy", async ({
  page,
}) => {
  await page.goto("/");

  // Hero headline is unchanged (the <br/>-split h1 is matched reliably by
  // "planner"); the subcopy now frames a conversation with turns rather
  // than a single "sourced answer".
  await expect(
    page.getByRole("heading", { level: 1 }).filter({ hasText: /planner/i }),
  ).toBeVisible();

  // The hero region speaks conversation + turns, with no retired
  // per-answer vocabulary.
  const hero = page.locator("section").first();
  await expect(hero).toContainText(/conversation in plain English/i);
  await expect(hero).toContainText(/turns once the advisor replies/i);
  await expect(hero).not.toContainText(/per answer/i);
});
