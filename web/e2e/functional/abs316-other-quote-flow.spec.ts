// Functional: ABS-316 off-menu "Other" question — free quote → buy → answer.
//
// Exercises the off-menu priced-question path through the real FastAPI
// stack (advisor.billing.quote + advisor.billing.answers + Postgres +
// MockGateway) via the /v1/_test/buy-answer/* harness. Billing stays
// dormant in e2e (no Stripe), so these endpoints drive the real services
// with a MockStripeClient and an ad-hoc (price_data) amount.
//
// Covered: a free-form question is QUOTED for free (no charge); the quote
// is anchored to the launch band; buying re-quotes server-side and
// authorizes the card; a grounded answer CAPTURES; an ungroundable one
// VOIDS (failed-question rule applies off-menu too).

import { expect, test } from "@playwright/test";

import { E2E_API_URL } from "../fixtures/test-env";

function uniqueUser(tag: string): string {
  return `abs316-${tag}-${Date.now()}-${Math.random()
    .toString(36)
    .slice(2, 8)}`;
}

async function quote(
  request: import("@playwright/test").APIRequestContext,
  question: string,
) {
  const res = await request.post(`${E2E_API_URL}/v1/_test/buy-answer/quote`, {
    data: { question },
  });
  expect(res.status(), await res.text()).toBe(200);
  return res.json();
}

async function checkoutOther(
  request: import("@playwright/test").APIRequestContext,
  userId: string,
  question: string,
) {
  const res = await request.post(
    `${E2E_API_URL}/v1/_test/buy-answer/checkout-other`,
    { data: { user_id: userId, question } },
  );
  expect(res.status(), await res.text()).toBe(200);
  return res.json();
}

async function runAnswer(
  request: import("@playwright/test").APIRequestContext,
  purchaseId: number,
) {
  const res = await request.post(`${E2E_API_URL}/v1/_test/buy-answer/answer`, {
    data: { purchase_id: purchaseId },
  });
  expect(res.status(), await res.text()).toBe(200);
  return res.json();
}

test("free quote anchors an off-menu question to the launch band", async ({
  request,
}) => {
  const simple = await quote(
    request,
    "Is a backyard suite allowed at 12 Pine St? MOCK_QUOTE_SIMPLE",
  );
  expect(simple.difficulty).toBe("simple");
  expect(simple.price_cents).toBe(7900); // $79 — the menu floor
  expect(simple.band_low_cents).toBe(7900);
  expect(simple.band_high_cents).toBe(29900);
  expect(simple.rationale).toBeTruthy();

  const complex = await quote(
    request,
    "Full variance justification with hardship analysis. MOCK_QUOTE_COMPLEX",
  );
  expect(complex.difficulty).toBe("complex");
  expect(complex.price_cents).toBe(29900); // $299 — top of the band
});

test("free-form question is quoted, purchased, and answered", async ({
  request,
}) => {
  const userId = uniqueUser("happy");
  const question =
    "What approvals would a rooftop patio need at 88 Water St? " +
    "MOCK_QUOTE_INVOLVED";

  // 1. Free quote — no charge, just a price.
  const q = await quote(request, question);
  expect(q.difficulty).toBe("involved");
  expect(q.price_cents).toBe(19900); // $199

  // 2. Buy: server re-quotes and authorizes the card at the quoted price.
  const bought = await checkoutOther(request, userId, question);
  expect(bought.status).toBe("authorized");
  expect(bought.question_slug).toBe("other");
  expect(bought.price_cents).toBe(19900);
  expect(bought.difficulty).toBe("involved");

  // 3. Answer: grounded → the hold is captured and the answer returned.
  const answered = await runAnswer(request, bought.purchase_id);
  expect(answered.status).toBe("captured");
  expect(answered.answer).toBeTruthy();
  expect(answered.failure_reason).toBeNull();
  // A fresh purchase opens the refinement window.
  expect(answered.refinements_remaining).toBe(3);
});

test("an ungroundable off-menu answer voids the authorization", async ({
  request,
}) => {
  const userId = uniqueUser("void");
  // MOCK_UNGROUNDABLE flows into the answer prompt → zero grounding
  // evidence → failed question → void (no charge).
  const bought = await checkoutOther(
    request,
    userId,
    "An off-menu question with no grounding MOCK_UNGROUNDABLE",
  );
  expect(bought.status).toBe("authorized");

  const answered = await runAnswer(request, bought.purchase_id);
  expect(answered.status).toBe("voided");
  expect(answered.failure_reason).toBe("zero_evidence");
  expect(answered.answer).toBeNull();
});
