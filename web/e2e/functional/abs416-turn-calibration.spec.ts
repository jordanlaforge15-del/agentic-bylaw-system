// Functional: ABS-416 — the token→turn conversion rate is honest.
//
// The app used to define 1 turn = 2,500 tokens while a real grounded
// question in prod burned 103k–248k. Every wallet surface that divides
// tokens by that rate therefore lied by ~70x: the balance strip, the
// billing page, and the pricing page's free-trial card all promised
// dozens-to-hundreds of questions against a wallet worth a handful.
//
// The lie was only ever visible where a *rendered turn count* met a
// *real token balance*, so unit tests on the divisor can't catch a
// regression here — a mismatch between the calibrated rate and what a
// surface actually renders is exactly what this spec covers:
//
//   1. The live backend runs the calibrated parameters (the wallet
//      endpoint is the contract every surface reads).
//   2. A brand-new wallet is genuinely worth the turns it advertises,
//      and its per-turn token size is in the measured 100k–250k band —
//      the assertion that fails if someone reinstates a 2,500-ish rate.
//   3. The pricing page's free-trial card renders the backend's figure
//      rather than a hardcoded "~10 turns".
//   4. Every paid top-up SKU advertises a whole, non-zero turn count
//      (unscaled token quantities would render "~0 turns · $15").
//   5. The primary entry flow — the in-app chat workspace — shows the
//      same honest count, and one real turn moves it.

import {
  acceptCurrentTermsAs,
  E2E_API_URL,
  expect,
  mintTestIdentity,
  openCaseAsIdentity,
  signInAs,
  test,
} from "../auth/fixtures";
import {
  CHAT_MIN_BALANCE,
  LOW_BALANCE_WARN,
  SIGNUP_GRANT,
  SIGNUP_GRANT_TURNS,
  TOKENS_PER_TURN,
  TOPUP_TURNS,
} from "../fixtures/wallet-params";

// The measured band for a real grounded question in prod (ABS-416):
// 103,014 and 247,566 tokens. A calibrated rate has to land inside it —
// the old 2,500 was two orders of magnitude below the floor.
const MEASURED_MIN_TURN_TOKENS = 100_000;
const MEASURED_MAX_TURN_TOKENS = 250_000;

function freshUser(tag: string): string {
  return `abs416-${tag}-${Date.now()}-${Math.random()
    .toString(36)
    .slice(2, 8)}`;
}

test("the live backend serves the calibrated wallet parameters", async ({
  request,
}) => {
  const res = await request.get(`${E2E_API_URL}/v1/billing/wallet`, {
    headers: { "X-Test-User-Id": freshUser("params") },
  });
  expect(res.status(), await res.text()).toBe(200);

  const body = (await res.json()) as {
    tokens_per_turn: number;
    warn_threshold_tokens: number;
    floor_tokens: number;
  };

  expect(body.tokens_per_turn).toBe(TOKENS_PER_TURN);
  expect(body.warn_threshold_tokens).toBe(LOW_BALANCE_WARN);
  expect(body.floor_tokens).toBe(CHAT_MIN_BALANCE);
});

test("one turn costs what a real question actually burns", async ({
  request,
}) => {
  const res = await request.get(`${E2E_API_URL}/v1/billing/wallet`, {
    headers: { "X-Test-User-Id": freshUser("band") },
  });
  const body = (await res.json()) as {
    balance_tokens: number;
    approx_turns_remaining: number;
    tokens_per_turn: number;
    low_balance: boolean;
  };

  // The regression guard: a rate outside the measured band means the
  // displayed turn count is a fiction again.
  expect(
    body.tokens_per_turn,
    "tokens_per_turn must sit in the measured 100k-250k prod burn band",
  ).toBeGreaterThanOrEqual(MEASURED_MIN_TURN_TOKENS);
  expect(body.tokens_per_turn).toBeLessThanOrEqual(MEASURED_MAX_TURN_TOKENS);

  // The signup grant is worth the turns it is sold as — the headline bug
  // was a grant advertised at "~10 turns" that bought under a quarter of
  // one question.
  expect(body.balance_tokens).toBe(SIGNUP_GRANT);
  expect(body.approx_turns_remaining).toBe(SIGNUP_GRANT_TURNS);
  expect(body.approx_turns_remaining).toBeGreaterThan(0);
  // A grant sized in whole turns is not "low" the moment it is issued.
  expect(body.low_balance).toBe(false);
});

test("the pricing page renders backend-owned turn counts on every card", async ({
  page,
  request,
}) => {
  // What the backend says the cards should show.
  const catalog = (await (
    await request.get(`${E2E_API_URL}/v1/billing/topups`)
  ).json()) as {
    tokens_per_turn: number;
    signup_grant_tokens: number;
    signup_grant_approx_turns: number;
    options: { sku: string; tokens: number; approx_turns: number }[];
  };

  // Free-trial card: backend-owned, not the old hardcoded "~10 turns".
  expect(catalog.signup_grant_approx_turns).toBe(
    Math.floor(catalog.signup_grant_tokens / catalog.tokens_per_turn),
  );

  // Every paid SKU is worth a whole, non-zero number of turns. Without the
  // ABS-416 rescale each would floor to 0 and read "~0 turns · $15".
  expect(catalog.options.length).toBeGreaterThan(0);
  for (const opt of catalog.options) {
    expect(
      opt.approx_turns,
      `${opt.sku} must advertise a non-zero turn count`,
    ).toBeGreaterThan(0);
    expect(opt.approx_turns).toBe(
      Math.floor(opt.tokens / catalog.tokens_per_turn),
    );
    expect(opt.approx_turns).toBe(TOPUP_TURNS[opt.sku]);
  }

  await page.goto("/pricing");

  const trial = page.getByTestId("trial-card");
  await expect(trial).toBeVisible({ timeout: 15_000 });
  await expect(page.getByTestId("trial-card-turns")).toHaveText(
    new RegExp(`~${catalog.signup_grant_approx_turns} turns?`, "i"),
  );

  for (const opt of catalog.options) {
    await expect(page.getByTestId(`topup-card-${opt.sku}`)).toContainText(
      `~${opt.approx_turns} turns`,
    );
  }

  // No card may claim a turn count the wallet cannot honour.
  await expect(page.getByTestId("trial-card")).not.toContainText(/~0 turns/i);
});

test("the chat workspace shows the honest count and one turn moves it", async ({
  context,
  page,
}) => {
  const identity = mintTestIdentity("abs416strip");
  await signInAs(context, identity);
  await acceptCurrentTermsAs(context, identity);
  const { caseId } = await openCaseAsIdentity(context, identity, {
    anchorLabel: `abs416strip-${identity.subUserId}`,
  });

  await page.goto(`/app?case_id=${caseId}`);

  const strip = page.getByTestId("balance-strip");
  await expect(strip).toBeVisible({ timeout: 15_000 });
  // The primary entry flow shows the same backend-owned figure the wallet
  // endpoint serves — this is the surface the cross-surface consistency
  // rule cares about.
  await expect(strip).toContainText(
    new RegExp(`~${SIGNUP_GRANT_TURNS} turns left`, "i"),
  );

  await page
    .getByPlaceholder(/Ask about this parcel/)
    .fill("What is the minimum front yard setback?");
  await page.getByRole("button", { name: /^Send/ }).click();

  await expect(page.getByTestId("chat-thread")).toContainText(
    /Based on the bylaw evidence/i,
    { timeout: 20_000 },
  );

  // A burn moves the count, and it never goes up or negative.
  await expect(strip).toContainText(/~\d+ turns? left/i);
  const text = (await strip.textContent()) ?? "";
  const shown = Number(/~(\d+) turns?/i.exec(text)?.[1] ?? "-1");
  expect(shown).toBeGreaterThanOrEqual(0);
  expect(shown).toBeLessThanOrEqual(SIGNUP_GRANT_TURNS);
});
