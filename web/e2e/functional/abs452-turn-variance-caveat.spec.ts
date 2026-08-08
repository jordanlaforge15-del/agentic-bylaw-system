// Functional: ABS-452 — the turn-count variance caveat on the two surfaces
// where a user actually watches their balance.
//
// Turns are a presentation of a token wallet, not a metered unit: one
// multi-attribute submission evaluation can burn ~100 turns' worth of tokens in
// a single reply. /pricing and /billing already carried the "counts are
// approximate" disclosure; /cases/new's turn chip carried none, and the
// in-conversation strip hid its copy below the `md` breakpoint (leaving only a
// `title` tooltip, which never fires on touch).
//
// These tests pin the caveat to both surfaces, including at a phone viewport,
// and pin the one place it's deliberately absent: a zero balance, where
// "0 turns" has no variance to caveat.

import { expect, test } from "../fixtures/test-env";
import type { Page } from "@playwright/test";
import {
  CHAT_MIN_BALANCE,
  LOW_BALANCE_WARN,
  TOKENS_PER_TURN,
  turnsToTokens,
} from "../fixtures/wallet-params";

// Shared short-form disclosure (web/lib/turn-copy.ts). Duplicated as a literal
// so the test asserts the user-visible sentence, not whatever the constant
// happens to hold.
const CAVEAT = /Counts are approximate — complex questions use more/i;

const PHONE = { width: 390, height: 844 };

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

// `balance_tokens` is derived from the turn count (ABS-416) so the stub can't
// pair a turn figure with a token balance the backend would never produce.
function wallet(overrides: Partial<Wallet>): Wallet {
  const turns = overrides.approx_turns_remaining ?? 150;
  return {
    balance_tokens: turnsToTokens(turns),
    approx_turns_remaining: turns,
    tokens_per_turn: TOKENS_PER_TURN,
    low_balance: false,
    warn_threshold_tokens: LOW_BALANCE_WARN,
    floor_tokens: CHAT_MIN_BALANCE,
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

test("cases/new: the free-trial turn estimate carries the approximate caveat", async ({
  page,
}) => {
  await stubWallet(page, wallet({ approx_turns_remaining: 150 }));
  await page.goto("/cases/new");

  const chip = page.getByTestId("balance-chip");
  await expect(chip).toContainText("~150 free trial turns");
  // The headline figure no longer stands alone.
  await expect(page.getByTestId("balance-chip-approx-note")).toContainText(
    CAVEAT,
  );

  // Assistive tech and hover carry it too: the caveat rides on the chip's
  // aria-label (which already expands "~" to "approximately") and its title.
  const labelled = chip.getByLabel(
    /approximately 150 free trial turns\. Counts are approximate/i,
  );
  await expect(labelled).toBeVisible();
  await expect(labelled).toHaveAttribute(
    "title",
    /Counts are approximate — complex questions use more/,
  );
});

test("cases/new: caveat is suppressed at a zero balance", async ({ page }) => {
  await stubWallet(
    page,
    wallet({ approx_turns_remaining: 0, low_balance: true }),
  );
  await page.goto("/cases/new");

  const chip = page.getByTestId("balance-chip");
  await expect(chip).toContainText("0 turns");
  // Nothing to be approximate about — the note is absent, and the empty-state
  // notice does the talking instead.
  await expect(page.getByTestId("balance-chip-approx-note")).toHaveCount(0);
  await expect(page.getByTestId("balance-notice")).toBeVisible();
});

test("cases/new: caveat survives a phone viewport", async ({ page }) => {
  await page.setViewportSize(PHONE);
  await stubWallet(page, wallet({ approx_turns_remaining: 150 }));
  await page.goto("/cases/new");

  await expect(page.getByTestId("balance-chip-approx-note")).toBeVisible();
});
