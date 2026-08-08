// Functional: ABS-386 — chat workspace BalanceStrip.
//
// The strip replaces the retired tier/budget header strip. It surfaces the
// account token wallet as *turns* only — never raw token counts, never tier
// vocabulary — seeded from GET /api/billing/wallet and decremented live off
// the per-turn ``token_balance`` SSE event.
//
// Drives the real Next.js proxy end to end (browser → /api/billing/wallet and
// /api/chat → FastAPI) for a freshly minted identity, which starts at the
// signup grant. The e2e stack runs billing DORMANT (payments off).

import {
  acceptCurrentTermsAs,
  expect,
  mintTestIdentity,
  openCaseAsIdentity,
  signInAs,
  test,
} from "../auth/fixtures";
import {
  SIGNUP_GRANT,
  SIGNUP_GRANT_TURNS,
} from "../fixtures/wallet-params";

const INITIAL_TURNS = SIGNUP_GRANT_TURNS; // 10

// Tier display names the strip must never render (retired vocabulary). The
// word "complex" on its own is intentionally NOT matched — it appears
// legitimately in the approximate disclosure ("complex questions use more").
const TIER_VOCAB = /tier|Quick Lookup|Standard Case|Complex File|budget/i;

test("strip shows Case # and ~turns seeded from the wallet, no token/tier copy", async ({
  context,
  page,
}) => {
  const identity = mintTestIdentity("abs386bal");
  await signInAs(context, identity);
  await acceptCurrentTermsAs(context, identity);
  const { caseId } = await openCaseAsIdentity(context, identity, {
    anchorLabel: `abs386bal-${identity.subUserId}`,
  });

  await page.goto(`/app?case_id=${caseId}`);

  const strip = page.getByTestId("balance-strip");
  await expect(strip).toBeVisible({ timeout: 10_000 });
  await expect(strip).toContainText(/Case #/i);
  await expect(strip).toContainText(
    new RegExp(`~${INITIAL_TURNS} turns left`, "i"),
  );
  // Standard approximate disclosure is present.
  await expect(strip).toContainText(
    /Counts are approximate — complex questions use more/i,
  );
  // No raw token counts and no tier vocabulary anywhere in the DOM copy.
  // Raw token counts never appear — neither the grant nor the divisor.
  await expect(strip).not.toContainText(
    new RegExp(`${SIGNUP_GRANT.toLocaleString("en-CA")}|tokens?\\b`, "i"),
  );
  await expect(strip).not.toContainText(TIER_VOCAB);
});

// ABS-452: the disclosure used to be `hidden md:inline`, so the one surface
// where the balance visibly drops mid-conversation showed no caveat at all on a
// phone — the `title` tooltip that was meant to cover it never fires on touch.
test("strip keeps the approximate disclosure at a phone viewport", async ({
  context,
  page,
}) => {
  const identity = mintTestIdentity("abs452strip");
  await signInAs(context, identity);
  await acceptCurrentTermsAs(context, identity);
  const { caseId } = await openCaseAsIdentity(context, identity, {
    anchorLabel: `abs452strip-${identity.subUserId}`,
  });

  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto(`/app?case_id=${caseId}`);

  const strip = page.getByTestId("balance-strip");
  await expect(strip).toBeVisible({ timeout: 10_000 });
  // Both the figure and its caveat are on screen, not one without the other.
  await expect(strip).toContainText(/~\d+ turns? left/i);
  await expect(page.getByTestId("balance-approx-note")).toBeVisible();
});

test("strip decrements live on the token_balance SSE event (no reload)", async ({
  context,
  page,
}) => {
  const identity = mintTestIdentity("abs386dec");
  await signInAs(context, identity);
  await acceptCurrentTermsAs(context, identity);
  const { caseId } = await openCaseAsIdentity(context, identity, {
    anchorLabel: `abs386dec-${identity.subUserId}`,
  });

  await page.goto(`/app?case_id=${caseId}`);
  const strip = page.getByTestId("balance-strip");
  await expect(strip).toContainText(
    new RegExp(`~${INITIAL_TURNS} turns left`, "i"),
    { timeout: 10_000 },
  );

  // Send one turn through the UI. Any positive burn drops turns below 10.
  await page
    .getByPlaceholder(/Ask about this parcel/)
    .fill("What is the minimum front yard setback?");
  await page.getByRole("button", { name: /^Send/ }).click();

  // Assistant reply arrives...
  await expect(page.getByTestId("chat-thread")).toContainText(
    /Based on the bylaw evidence/i,
    { timeout: 15_000 },
  );
  // ...and the strip has updated WITHOUT a reload — turns are now < 10.
  await expect(strip).not.toContainText(
    new RegExp(`~${INITIAL_TURNS} turns left`),
    { timeout: 10_000 },
  );
  await expect(strip).toContainText(/~\d+ turns? left/i);
});
