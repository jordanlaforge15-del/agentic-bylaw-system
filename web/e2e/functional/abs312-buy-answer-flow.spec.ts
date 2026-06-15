// Functional: ABS-312 priced-question "buy an answer" flow.
//
// Exercises the full failed-question + refinement-window mechanism
// through the real FastAPI stack (service + Postgres + MockGateway) via
// the /v1/_test/buy-answer/* harness. Billing stays dormant in e2e (no
// Stripe), so these test endpoints drive advisor.billing.answers with a
// MockStripeClient — the same pattern the evaluator/address-profile
// endpoints use for heavy/external-dependency code paths.
//
// Covered: purchase+capture on a grounded answer; VOID on a failed
// (ungroundable) question; in-window refinement served; window
// exhaustion; new-question follow-up blocked and routed to a new
// purchase.

import { expect, test } from "@playwright/test";

import { E2E_API_URL } from "../fixtures/test-env";

function uniqueUser(tag: string): string {
  return `abs312-${tag}-${Date.now()}-${Math.random()
    .toString(36)
    .slice(2, 8)}`;
}

const VALID_INPUTS = {
  address: "1234 Elm St",
  proposed_use: "a four-unit dwelling",
};

async function checkout(
  request: import("@playwright/test").APIRequestContext,
  userId: string,
  inputs: Record<string, string>,
  slug = "permitted_use",
) {
  const res = await request.post(`${E2E_API_URL}/v1/_test/buy-answer/checkout`, {
    data: { user_id: userId, question_slug: slug, inputs },
  });
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

async function refine(
  request: import("@playwright/test").APIRequestContext,
  purchaseId: number,
  message: string,
) {
  const res = await request.post(`${E2E_API_URL}/v1/_test/buy-answer/refine`, {
    data: { purchase_id: purchaseId, message },
  });
  expect(res.status(), await res.text()).toBe(200);
  return res.json();
}

test("purchase authorizes, grounded answer captures the card", async ({
  request,
}) => {
  const userId = uniqueUser("capture");
  const created = await checkout(request, userId, VALID_INPUTS);
  // Checkout + webhook authorization landed the card hold.
  expect(created.status).toBe("authorized");
  expect(created.question_slug).toBe("permitted_use");

  const answered = await runAnswer(request, created.purchase_id);
  expect(answered.status).toBe("captured");
  expect(answered.answer).toBeTruthy();
  expect(answered.failure_reason).toBeNull();
  // A fresh purchase opens the refinement window with 3 follow-ups.
  expect(answered.refinements_remaining).toBe(3);
  expect(answered.window_expires_at).toBeTruthy();
});

test("ungroundable question voids the authorization (no charge)", async ({
  request,
}) => {
  const userId = uniqueUser("void");
  const created = await checkout(request, userId, {
    address: "1234 Elm St",
    // MOCK_UNGROUNDABLE flows into the prompt → the engine answers with
    // zero grounding evidence → failed question → void.
    proposed_use: "a four-unit dwelling MOCK_UNGROUNDABLE",
  });
  expect(created.status).toBe("authorized");

  const answered = await runAnswer(request, created.purchase_id);
  expect(answered.status).toBe("voided");
  expect(answered.failure_reason).toBe("zero_evidence");
  expect(answered.answer).toBeNull();
});

test("in-window refinement is served free", async ({ request }) => {
  const userId = uniqueUser("refine");
  const created = await checkout(request, userId, VALID_INPUTS);
  await runAnswer(request, created.purchase_id);

  const refined = await refine(
    request,
    created.purchase_id,
    "Summarize the answer in three bullet points.",
  );
  expect(refined.ok).toBe(true);
  expect(refined.answer).toBeTruthy();
  expect(refined.refinement_count).toBe(1);
  expect(refined.refinements_remaining).toBe(2);
});

test("refinement window exhausts after the follow-up budget", async ({
  request,
}) => {
  const userId = uniqueUser("exhaust");
  const created = await checkout(request, userId, VALID_INPUTS);
  await runAnswer(request, created.purchase_id);

  for (let i = 0; i < 3; i++) {
    const r = await refine(
      request,
      created.purchase_id,
      `Please reformat the answer (pass ${i + 1}).`,
    );
    expect(r.ok).toBe(true);
  }

  // The 4th follow-up is over budget → blocked, routed to a new purchase.
  const blocked = await refine(
    request,
    created.purchase_id,
    "One more reformat please.",
  );
  expect(blocked.ok).toBe(false);
  expect(blocked.code).toBe("window_exhausted");
  expect(blocked.reason).toBe("refinements_exhausted");
});

test("a materially new question is blocked and routed to a new purchase", async ({
  request,
}) => {
  const userId = uniqueUser("newq");
  const created = await checkout(request, userId, VALID_INPUTS);
  await runAnswer(request, created.purchase_id);

  // A different address is a different question — must not be served free.
  const blocked = await refine(
    request,
    created.purchase_id,
    "What about 999 Oak Avenue instead?",
  );
  expect(blocked.ok).toBe(false);
  expect(blocked.code).toBe("new_question");
  expect(blocked.suggested_slug).toBe("permitted_use");
  // The blocked follow-up did not consume a refinement.
  expect(blocked.refinement_count).toBe(0);
});

test("unworkable inputs are rejected before any charge", async ({
  request,
}) => {
  const userId = uniqueUser("badinput");
  const res = await request.post(
    `${E2E_API_URL}/v1/_test/buy-answer/checkout`,
    {
      data: {
        user_id: userId,
        question_slug: "permitted_use",
        inputs: { address: "1234 Elm St" }, // proposed_use missing
      },
    },
  );
  expect(res.status()).toBe(400);
  const body = await res.json();
  expect(body.detail.code).toBe("missing_required_inputs");
  expect(body.detail.missing).toContain("proposed_use");
});
