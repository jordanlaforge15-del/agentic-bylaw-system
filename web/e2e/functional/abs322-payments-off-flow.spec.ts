// Functional: ABS-322 payments-off / free-trial buy-an-answer flow.
//
// The go-live config: ADVISOR_PAYMENTS_ENABLED=false. An answer is
// unlocked by consuming a free-question credit (ABS-314), NOT a Stripe
// charge — no checkout session, no bank account. This spec drives the
// real advisor.billing.answers service through the /v1/_test/buy-answer/*
// harness (sqlite-free: real test Postgres + MockGateway), exercising:
//
//   signup grant → pick question → answer → refine → exhaustion
//
// plus the failed-question refund rule (an ungroundable question hands
// the reserved credit back, so the trial user is no worse off).

import { expect, test } from "@playwright/test";

import { E2E_API_URL } from "../fixtures/test-env";

function uniqueUser(tag: string): string {
  return `abs322-${tag}-${Date.now()}-${Math.random()
    .toString(36)
    .slice(2, 8)}`;
}

const VALID_INPUTS = {
  address: "1234 Elm St",
  proposed_use: "a four-unit dwelling",
};

type Req = import("@playwright/test").APIRequestContext;

async function grantFree(request: Req, userId: string, quantity: number) {
  const res = await request.post(
    `${E2E_API_URL}/v1/_test/buy-answer/grant-free-questions`,
    { data: { user_id: userId, quantity } },
  );
  expect(res.status(), await res.text()).toBe(200);
  return res.json();
}

async function freeBalance(request: Req, userId: string): Promise<number> {
  const res = await request.post(
    `${E2E_API_URL}/v1/_test/buy-answer/free-balance`,
    { data: { user_id: userId } },
  );
  expect(res.status(), await res.text()).toBe(200);
  return (await res.json()).free_questions_remaining as number;
}

async function checkoutFree(
  request: Req,
  userId: string,
  inputs: Record<string, string>,
  slug = "permitted_use",
) {
  const res = await request.post(
    `${E2E_API_URL}/v1/_test/buy-answer/checkout-free`,
    { data: { user_id: userId, question_slug: slug, inputs } },
  );
  expect(res.status(), await res.text()).toBe(200);
  return res.json();
}

async function runAnswer(request: Req, purchaseId: number) {
  const res = await request.post(
    `${E2E_API_URL}/v1/_test/buy-answer/answer`,
    { data: { purchase_id: purchaseId } },
  );
  expect(res.status(), await res.text()).toBe(200);
  return res.json();
}

async function refine(request: Req, purchaseId: number, message: string) {
  const res = await request.post(
    `${E2E_API_URL}/v1/_test/buy-answer/refine`,
    { data: { purchase_id: purchaseId, message } },
  );
  expect(res.status(), await res.text()).toBe(200);
  return res.json();
}

test("free-trial: grant → answer → refine, consuming one credit", async ({
  request,
}) => {
  const userId = uniqueUser("happy");
  // Signup-equivalent grant of 2 free questions.
  const granted = await grantFree(request, userId, 2);
  expect(granted.free_questions_remaining).toBe(2);

  // Pick a question — consumes one credit, lands authorized with NO Stripe.
  const created = await checkoutFree(request, userId, VALID_INPUTS);
  expect(created.ok).toBe(true);
  expect(created.status).toBe("authorized");
  expect(created.free_questions_remaining).toBe(1);

  // Run the answer → grounded → captured (no charge, credit stays spent).
  const answered = await runAnswer(request, created.purchase_id);
  expect(answered.status).toBe("captured");
  expect(answered.answer).toBeTruthy();
  expect(answered.refinements_remaining).toBe(3);

  // A refinement is served free under the same purchase — no extra credit.
  const refined = await refine(
    request,
    created.purchase_id,
    "Summarize the answer in three bullet points.",
  );
  expect(refined.ok).toBe(true);
  expect(refined.refinement_count).toBe(1);
  expect(await freeBalance(request, userId)).toBe(1);
});

test("free-trial: exhaustion shows the 'coming soon' state", async ({
  request,
}) => {
  const userId = uniqueUser("exhaust");
  // Exactly one free question.
  await grantFree(request, userId, 1);

  // Spend it on a grounded answer.
  const created = await checkoutFree(request, userId, VALID_INPUTS);
  expect(created.ok).toBe(true);
  const answered = await runAnswer(request, created.purchase_id);
  expect(answered.status).toBe("captured");
  expect(await freeBalance(request, userId)).toBe(0);

  // Next checkout has no credit → exhaustion state (not a dead checkout).
  const exhausted = await checkoutFree(request, userId, VALID_INPUTS);
  expect(exhausted.ok).toBe(false);
  expect(exhausted.code).toBe("free_questions_exhausted");
  expect(exhausted.free_questions_remaining).toBe(0);
});

test("failed-question rule: an ungroundable answer refunds the credit", async ({
  request,
}) => {
  const userId = uniqueUser("refund");
  await grantFree(request, userId, 1);

  // MOCK_UNGROUNDABLE flows into the prompt → zero grounding tool calls.
  const created = await checkoutFree(request, userId, {
    address: "1234 Elm St",
    proposed_use: "a four-unit dwelling MOCK_UNGROUNDABLE",
  });
  expect(created.ok).toBe(true);
  // The credit is reserved while the question runs.
  expect(created.free_questions_remaining).toBe(0);

  const answered = await runAnswer(request, created.purchase_id);
  expect(answered.status).toBe("voided");
  expect(answered.failure_reason).toBe("zero_evidence");
  expect(answered.answer).toBeNull();

  // Reserved credit is handed back — the trial user is no worse off.
  expect(await freeBalance(request, userId)).toBe(1);
});
