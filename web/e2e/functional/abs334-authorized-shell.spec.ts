// Functional: ABS-334 — the authorized navigation shell + redesigned
// "Open a case" page.
//
// Two product changes are pinned here:
//   1. The shared workspace menu (AccountMenu) is the single authorized
//      nav control. It appears on /app (compact), /cases/new, and
//      /app/billing (in the AuthBar), and every nav row routes correctly.
//   2. The redesigned /cases/new page renders the priced-question cards on
//      the design system with a live checkout summary footer whose price +
//      "YOUR ANSWER" name track the selected question.
//
// The catalog wiring itself (live menu, free-trial gating) is covered by
// abs320; this spec guards the new navigation + presentation layer.

import { expect, test } from "../fixtures/test-env";

test("workspace menu routes from /app to Open a case", async ({ page }) => {
  await page.goto("/app");

  // The compact workspace menu lives in the app header.
  const trigger = page.getByRole("button", { name: "Workspace menu" });
  await expect(trigger).toBeVisible();
  await trigger.click();

  // The dropdown shows the grouped destinations.
  const menu = page.getByRole("menu");
  await expect(menu).toBeVisible();
  await expect(menu.getByText("WORKSPACE")).toBeVisible();
  await expect(menu.getByText("REFERENCE")).toBeVisible();

  // Readings is the active row while we're on /app.
  await expect(
    menu.getByRole("menuitem", { name: /Readings/ }),
  ).toHaveAttribute("aria-current", "page");

  // Navigating to Open a case lands on /cases/new (ABS-385: the page is the
  // conversation-first "Start a conversation." surface).
  await menu.getByRole("menuitem", { name: /Open a case/ }).click();
  await page.waitForURL(/\/cases\/new/);
  await expect(
    page.getByRole("heading", { name: /Start a conversation/ }),
  ).toBeVisible();
});

test("sidebar primary action is '+ Open a case' and routes to /cases/new", async ({
  page,
}) => {
  await page.goto("/app");
  // The desktop sidebar's primary button is relabeled from "+ New reading".
  const openCase = page.getByRole("button", { name: "+ Open a case" });
  await expect(openCase).toBeVisible();
  await expect(
    page.getByRole("button", { name: "+ New reading" }),
  ).toHaveCount(0);
  await openCase.click();
  await page.waitForURL(/\/cases\/new/);
});

test("workspace menu closes on Escape", async ({ page }) => {
  await page.goto("/app");
  await page.getByRole("button", { name: "Workspace menu" }).click();
  await expect(page.getByRole("menu")).toBeVisible();
  await page.keyboard.press("Escape");
  await expect(page.getByRole("menu")).not.toBeVisible();
});

test("Open a case renders the redesigned conversation-first shell", async ({
  page,
}) => {
  await page.goto("/cases/new");

  // Authorized shell: the AuthBar carries its own workspace menu and the
  // ACCOUNT · NEW CONVERSATION section label (not the marketing TopNav).
  await expect(
    page.getByRole("button", { name: "Workspace menu" }),
  ).toBeVisible();
  await expect(
    page.getByText("ACCOUNT · NEW CONVERSATION").first(),
  ).toBeVisible();

  // The free conversation entry is the primary surface.
  await expect(page.getByTestId("start-conversation-btn")).toBeVisible();

  // Released report SKUs render as the secondary accordion, each offer
  // carrying its per-report price in the header (collapsed, nothing
  // pre-selected).
  await expect(page.getByTestId("question-menu")).toBeVisible();
  await expect(
    page.getByTestId("question-option-due_diligence"),
  ).toContainText("$199");
  await expect(
    page.getByTestId("question-option-permitted_use"),
  ).toContainText("$79");
});

test("/app/billing renders inside the authorized shell", async ({ page }) => {
  await page.goto("/app/billing");

  // The AuthBar workspace menu is present and the section label reads
  // ACCOUNT · BILLING — the old bespoke "Back to chat" header is gone.
  await expect(
    page.getByRole("button", { name: "Workspace menu" }),
  ).toBeVisible();
  await expect(page.getByText("ACCOUNT · BILLING").first()).toBeVisible();
  await expect(page.getByText("Back to chat")).toHaveCount(0);
  await expect(
    page.getByRole("heading", { name: /Billing/ }),
  ).toBeVisible();
});
