// Functional: ABS-315 consultant-style LLM intake detection.
//
// When a selected catalog question is missing required inputs, an LLM
// extracts what it can from the conversation and the server decides
// completeness against the question's required-input schema — asking
// conversationally for whatever is still missing, then letting the
// buy-an-answer flow proceed once everything required is collected.
//
// Exercised through the real FastAPI stack (advisor.billing.intake +
// advisor.billing.answers + Postgres + MockGateway) via the
// /v1/_test/buy-answer/* harness. The MockGateway resolves a deterministic
// extraction from MOCK_INPUT[<field>]=<value>| sentinels in the
// conversation, so omitting a required field's sentinel forces an intake
// prompt and adding it on the next turn completes the intake.
//
// Covered: an incomplete question triggers a consultant prompt naming the
// missing field; supplying the input (with prior inputs carried forward)
// completes the intake for FREE (no charge); the collected inputs flow
// into checkout → authorize → grounded answer → capture. Optional inputs
// never block completion.

import { expect, test } from "@playwright/test";

import { E2E_API_URL } from "../fixtures/test-env";

function uniqueUser(tag: string): string {
  return `abs315-${tag}-${Date.now()}-${Math.random()
    .toString(36)
    .slice(2, 8)}`;
}

async function intake(
  request: import("@playwright/test").APIRequestContext,
  body: {
    question_slug: string;
    conversation?: string;
    inputs?: Record<string, string>;
  },
) {
  const res = await request.post(`${E2E_API_URL}/v1/_test/buy-answer/intake`, {
    data: body,
  });
  expect(res.status(), await res.text()).toBe(200);
  return res.json();
}

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

test("intake prompt -> user supplies input -> answer proceeds", async ({
  request,
}) => {
  const userId = uniqueUser("happy");

  // 1. Incomplete: the user describes a proposed use but no address. The
  //    consultant detects the missing address and asks for it. FREE — no
  //    purchase row, no card hold.
  const first = await intake(request, {
    question_slug: "permitted_use",
    conversation:
      "Can I put up a fourplex on my lot? " +
      "MOCK_INPUT[proposed_use]=a four-unit dwelling|",
  });
  expect(first.complete).toBe(false);
  expect(first.missing_required).toEqual(["address"]);
  expect(first.prompt).toContain("Property address");
  expect(first.inputs.proposed_use).toBe("a four-unit dwelling");

  // 2. The user replies with the address. The already-collected input is
  //    carried forward, so the merged set is now complete.
  const second = await intake(request, {
    question_slug: "permitted_use",
    conversation: "It's at 12 Pine Street. MOCK_INPUT[address]=12 Pine Street|",
    inputs: first.inputs,
  });
  expect(second.complete).toBe(true);
  expect(second.prompt).toBe("");
  expect(second.inputs).toEqual({
    address: "12 Pine Street",
    proposed_use: "a four-unit dwelling",
  });

  // 3. The collected inputs flow into checkout → authorize → answer. A
  //    question that earlier lacked inputs now proceeds to a captured,
  //    grounded answer.
  const bought = await checkout(request, userId, second.inputs);
  expect(bought.status).toBe("authorized");

  const answered = await runAnswer(request, bought.purchase_id);
  expect(answered.status).toBe("captured");
  expect(answered.answer).toBeTruthy();
  expect(answered.failure_reason).toBeNull();
});

test("intake completes in one turn when all inputs are present", async ({
  request,
}) => {
  const result = await intake(request, {
    question_slug: "permitted_use",
    conversation:
      "Is a duplex allowed at 12 Pine Street? " +
      "MOCK_INPUT[address]=12 Pine Street|MOCK_INPUT[proposed_use]=a duplex|",
  });
  expect(result.complete).toBe(true);
  expect(result.missing_required).toEqual([]);
  expect(result.prompt).toBe("");
  expect(result.inputs).toEqual({
    address: "12 Pine Street",
    proposed_use: "a duplex",
  });
});

test("an optional input does not block intake completion", async ({
  request,
}) => {
  // legal_nonconforming requires address + existing_use_or_structure;
  // establishment_date is optional. Supplying the two required inputs
  // completes intake while the optional one is surfaced, not demanded.
  const result = await intake(request, {
    question_slug: "legal_nonconforming",
    conversation:
      "MOCK_INPUT[address]=5 King St|" +
      "MOCK_INPUT[existing_use_or_structure]=a corner store|",
  });
  expect(result.complete).toBe(true);
  expect(result.missing_required).toEqual([]);
  expect(result.missing_optional).toContain("establishment_date");
});
