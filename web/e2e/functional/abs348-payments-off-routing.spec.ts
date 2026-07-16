// ABS-390 (reconciled from ABS-348): in the beta payments-OFF posture
// (ADVISOR_BILLING_ENABLED=true, ADVISOR_PAYMENTS_ENABLED=false) a gated report
// slug returns available:true / payments_enabled:false. Ordering a report must
// unlock via a free credit and land on the report in the /app workspace
// (/app?report_id=<id>) — NOT a Stripe checkout that returns no URL (the
// original ABS-348 dead-end, which silently consumed the credit and then showed
// "Checkout returned no URL. Try again.").
//
// The e2e stack runs billing DORMANT (available:false), which is why this bug
// was invisible: the available:true path never executed. Here we stub the menu
// to the payments-off posture and the me/intake/free-start calls, then assert
// the redesigned /cases/new report accordion (ABS-385) takes the
// free-start → answer-view path and never touches /checkout/question.
// Import the authenticated fixtures (mock-Clerk session), not raw
// @playwright/test — otherwise /cases/new redirects to sign-in.
import { expect, test } from "../fixtures/test-env";

test("payments-off: completing a question routes to the answer view via free-start, not Stripe", async ({
  page,
}) => {
  // Go-live posture: billing on, payments off, every question available.
  await page.route("**/api/billing/questions", async (route) => {
    const resp = await route.fetch();
    const body = await resp.json();
    body.enabled = true;
    body.payments_enabled = false;
    body.conversation_enabled = false; // Answers-only: no match-lookup noise.
    for (const q of body.questions) q.available = true;
    await route.fulfill({ response: resp, json: body });
  });

  // Deterministic free-credit balance so the free-trial CTA shows regardless
  // of what sibling tests did to the shared demo user.
  await page.route("**/api/billing/me", (route) =>
    route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        enabled: true,
        stripe_customer_id: null,
        tier_balances: [],
        total_available_credits: 0,
        free_questions_remaining: 3,
      }),
    }),
  );

  // Intake completes on the first pass.
  await page.route("**/api/billing/questions/intake", (route) =>
    route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        complete: true,
        prompt: null,
        inputs: { address: "1234 Main St, Halifax", proposed_use: "a duplex" },
      }),
    }),
  );

  // The Stripe path must NOT be hit in payments-off mode.
  let checkoutHit = false;
  await page.route("**/api/billing/checkout/question", (route) => {
    checkoutHit = true;
    return route.abort();
  });

  // Free-credit unlock returns a purchase id → the app routes to the answer view.
  await page.route("**/api/billing/questions/free-start", (route) =>
    route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        purchase_id: 4242,
        status: "authorized",
        free_questions_remaining: 2,
      }),
    }),
  );

  await page.goto("/cases/new");
  await expect(page.getByTestId("question-menu")).toBeVisible();

  await page.getByTestId("question-option-permitted_use").click();

  // Anchor the case (required before starting an answer).
  const anchor = page.getByPlaceholder(/1234 Main St, Halifax/);
  await anchor.fill("1234 Main St, Halifax");
  await anchor.blur();

  // Payments-off => the CTA is the free-trial affordance, not a "$79" button.
  const cta = page.getByTestId("free-trial-btn");
  await expect(cta).toBeVisible();

  await page
    .getByPlaceholder(/Tell us what you want answered/i)
    .fill("Is a duplex allowed at 1234 Main St?");
  await cta.click();

  // The fix: land on the report via the free-credit path (now the unified
  // /app workspace, ABS-344), never hitting the Stripe checkout.
  await page.waitForURL("**/app?report_id=4242");
  expect(
    checkoutHit,
    "payments-off must not hit the Stripe /checkout/question endpoint",
  ).toBe(false);
});
