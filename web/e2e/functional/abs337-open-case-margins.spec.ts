// Functional: ABS-337 — Open a case page margin fixes.
//
// Verifies that the two spacing gaps called out in the issue are present:
//   1. The CaseRadio checkbox and question title in each QuestionCard have
//      at least 12px of gap between them (design spec: 14px).
//   2. The AccountMenu trigger's monogram avatar and the name text have at
//      least 9px of gap between them (design spec: 11px).
//
// We measure the gap from computed layout (getBoundingClientRect) rather than
// hard-coding exact pixel values, so the test stays valid under minor
// typography or viewport changes.

import { expect, test } from "../fixtures/test-env";

test("QuestionCard checkbox and title have gap ≥ 12px", async ({ page }) => {
  await page.goto("/cases/new");

  // Wait for the question cards to appear.
  await expect(page.getByTestId("question-menu")).toBeVisible();

  // Grab the first question card.
  const firstCard = page
    .getByTestId("question-menu")
    .locator("[data-testid^='question-option-']")
    .first();
  await expect(firstCard).toBeVisible();

  // Measure the gap between the CaseRadio (aria-hidden span) and the bold
  // title span that follows it inside the flex row.
  const gap = await firstCard.evaluate((card) => {
    // The layout is: <div flex items-start gap-[14px]>
    //   <span aria-hidden>   ← CaseRadio
    //   <span>title</span>
    const row = card.querySelector<HTMLElement>("div.flex.items-start");
    if (!row) return null;
    const children = Array.from(row.children) as HTMLElement[];
    if (children.length < 2) return null;
    const radioRight = children[0].getBoundingClientRect().right;
    const titleLeft = children[1].getBoundingClientRect().left;
    return titleLeft - radioRight;
  });

  expect(gap).not.toBeNull();
  expect(gap!).toBeGreaterThanOrEqual(12);
});

test("AccountMenu trigger monogram and name text have gap ≥ 9px", async ({
  page,
}) => {
  await page.goto("/cases/new");

  const trigger = page.getByRole("button", { name: "Workspace menu" });
  await expect(trigger).toBeVisible();

  const gap = await trigger.evaluate((btn) => {
    // Trigger layout: <button inline-flex items-center gap-[11px]>
    //   <div>  ← Avatar (monogram square)
    //   <span> ← name + role text
    //   <span> ← chevron
    const children = Array.from(btn.children) as HTMLElement[];
    if (children.length < 2) return null;
    const avatarRight = children[0].getBoundingClientRect().right;
    const nameLeft = children[1].getBoundingClientRect().left;
    return nameLeft - avatarRight;
  });

  expect(gap).not.toBeNull();
  expect(gap!).toBeGreaterThanOrEqual(9);
});
