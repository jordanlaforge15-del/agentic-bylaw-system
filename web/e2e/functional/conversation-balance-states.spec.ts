// Functional: the BalanceChip + brick attention notice on the
// conversation-first /cases/new (ABS-385).
//
// The chip speaks in turns, never tokens (design D7). Healthy = accent dot +
// "~N turns left" (payments-off: "~N free trial turns"); low/empty = brick
// border + alarm glyph; empty label is "0 turns". A brick role=note notice
// renders only for low/empty, with the exact copy variant for each of
// {empty,low} × {payments on,off} and a "Top up turns →" ghost when payments
// are on. The free CTA still works in every state — the notice is guidance,
// not a gate. We stub GET /api/billing/wallet per case (the shared demo user
// carries a large balance otherwise).

import { expect, test } from "../fixtures/test-env";
import type { Page } from "@playwright/test";

type Wallet = {
  balance_tokens: number;
  approx_turns_remaining: number;
  tokens_per_turn: number;
  low_balance: boolean;
  warn_threshold_tokens: number;
  floor_tokens: number;
  chat_enabled: boolean;
  payments_enabled: boolean;
};

function wallet(overrides: Partial<Wallet>): Wallet {
  return {
    balance_tokens: 30_000,
    approx_turns_remaining: 12,
    tokens_per_turn: 2_500,
    low_balance: false,
    warn_threshold_tokens: 5_000,
    floor_tokens: 0,
    chat_enabled: true,
    payments_enabled: false,
    ...overrides,
  };
}

async function stubWallet(page: Page, w: Wallet) {
  await page.route("**/api/billing/wallet", (route) =>
    route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify(w),
    }),
  );
}

test("healthy (payments off): accent dot + free-trial turns, no notice", async ({
  page,
}) => {
  await stubWallet(page, wallet({ approx_turns_remaining: 12 }));
  await page.goto("/cases/new");

  const chip = page.getByTestId("balance-chip");
  await expect(chip).toContainText("~12 free trial turns");
  await expect(chip).toContainText("OPENING IS FREE");
  // aria-label expands the "~" for screen readers.
  await expect(chip.getByLabel(/approximately 12 free trial turns/)).toBeVisible();
  // Healthy → no alarm glyph, no brick notice.
  await expect(page.getByTestId("balance-alarm")).toHaveCount(0);
  await expect(page.getByTestId("balance-notice")).toHaveCount(0);
});

test("low (payments off): brick alarm + running-low notice, no top-up ghost", async ({
  page,
}) => {
  await stubWallet(
    page,
    wallet({ approx_turns_remaining: 2, low_balance: true, balance_tokens: 4_000 }),
  );
  await page.goto("/cases/new");

  await expect(page.getByTestId("balance-chip")).toContainText(
    "~2 free trial turns",
  );
  await expect(page.getByTestId("balance-alarm")).toBeVisible();

  const notice = page.getByTestId("balance-notice");
  await expect(notice).toHaveAttribute("role", "note");
  await expect(notice).toContainText(
    "You're running low on free trial turns. Opening is still free.",
  );
  await expect(page.getByTestId("top-up-turns-btn")).toHaveCount(0);
  // The free CTA still works.
  await expect(page.getByTestId("start-conversation-btn")).toBeVisible();
});

test("empty (payments off): 0 turns + used-up notice, no ghost, CTA usable", async ({
  page,
}) => {
  await stubWallet(
    page,
    wallet({ approx_turns_remaining: 0, low_balance: true, balance_tokens: 0 }),
  );
  await page.goto("/cases/new");

  await expect(page.getByTestId("balance-chip")).toContainText("0 turns");
  await expect(page.getByTestId("balance-alarm")).toBeVisible();
  await expect(page.getByTestId("balance-notice")).toContainText(
    "Opening is free, but replies need turns and your free trial is used up. Paid top-ups open soon.",
  );
  await expect(page.getByTestId("top-up-turns-btn")).toHaveCount(0);

  // Free CTA works in every state: enabled once an anchor is present.
  await page.getByPlaceholder(/1234 Main St, Halifax/).fill("500 Test Ave");
  await expect(page.getByTestId("start-conversation-btn")).toBeEnabled();
});

test("empty (payments on): out-of-turns notice with a top-up ghost to billing", async ({
  page,
}) => {
  await stubWallet(
    page,
    wallet({
      approx_turns_remaining: 0,
      low_balance: true,
      balance_tokens: 0,
      payments_enabled: true,
    }),
  );
  await page.goto("/cases/new");

  await expect(page.getByTestId("balance-chip")).toContainText("0 turns");
  await expect(page.getByTestId("balance-notice")).toContainText(
    "Opening is free, but replies need turns and you're out. Top up now or after you open.",
  );
  await expect(page.getByTestId("top-up-turns-btn")).toBeVisible();
});

test("low (payments on): running-low notice draws on last turns, top-up ghost", async ({
  page,
}) => {
  await stubWallet(
    page,
    wallet({
      approx_turns_remaining: 1,
      low_balance: true,
      balance_tokens: 2_500,
      payments_enabled: true,
    }),
  );
  await page.goto("/cases/new");

  await expect(page.getByTestId("balance-chip")).toContainText("~1 turns left");
  await expect(page.getByTestId("balance-notice")).toContainText(
    "You're running low. Opening is free; your first reply will draw on your last turns.",
  );
  await expect(page.getByTestId("top-up-turns-btn")).toBeVisible();
});
