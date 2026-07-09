// Functional: ABS-375 — the report agent can resolve the zoning of the
// parcels ABUTTING the subject parcel mid-run, so a setback that depends on
// the abutting zone gets a DEFINITIVE verdict instead of being punted to the
// customer ("UNCERTAIN — depends on adjacent zoning" / "verify with HRM").
//
// The gap:
//   The $149 development-standards and $299 variance reports repeatedly left
//   rear/side setback rows unresolved because the governing setback is
//   conditional on the NEIGHBOURING lot's zone, and the agent had no tool to
//   resolve it. get_address_profile only resolves the SUBJECT parcel.
//
// The fix:
//   A new grounding tool, get_adjacent_zoning, finds the subject parcel,
//   enumerates the abutting parcels, and resolves each neighbour's zone —
//   see src/advisor/chat/tools.py + RetrievalService.get_adjacent_zoning
//   (mcp/bylaw_retrieval/retrieval/service.py). It is registered in
//   answers.GROUNDING_TOOLS, so a turn that grounds via it commits the
//   reserved credit.
//
// What this spec guards (the PLUMBING, end-to-end):
//   A variance purchase whose input carries the MOCK_ADJACENT_ZONING
//   sentinel drives the MockGateway to (round 1) call get_adjacent_zoning
//   and (round 2) answer with a definitive, abutting-zone-grounded setback
//   verdict. The purchase runs through the REAL buy-an-answer service
//   (checkout → authorize → run → settle) over the e2e FastAPI + Postgres +
//   MockGateway stack and CAPTURES — proving the tool is dispatched, counts
//   as grounding, and the definitive-verdict answer path works. If the tool
//   were unregistered or dropped from GROUNDING_TOOLS the run would void as
//   zero-evidence and this spec turns red.
//
//   The neighbour-zone spatial resolution itself (parcel adjacency → zone
//   intersection) is a data-dependent property the MockGateway can't
//   reproduce, so it is pinned in the Python unit test
//   tests/test_get_adjacent_zoning.py.

import { expect, test } from "@playwright/test";

import { E2E_API_URL } from "../fixtures/test-env";

function uniqueUser(tag: string): string {
  return `abs375-${tag}-${Date.now()}-${Math.random()
    .toString(36)
    .slice(2, 8)}`;
}

// A routine downtown side-yard variance whose governing requirement depends
// on the abutting zone. The MOCK_ADJACENT_ZONING sentinel rides the free-text
// requested_variance slot so it reaches the dispatcher through the rendered
// prompt.
const VARIANCE_INPUTS = {
  address: "1250 Robie St, Halifax",
  requested_variance:
    "Reduce the required side-yard setback from 2.5 m to 0.0 m. " +
    "MOCK_ADJACENT_ZONING",
  hardship_rationale:
    "The lot abuts another downtown parcel on the east side.",
};

async function checkout(
  request: import("@playwright/test").APIRequestContext,
  userId: string,
) {
  const res = await request.post(
    `${E2E_API_URL}/v1/_test/buy-answer/checkout`,
    {
      data: {
        user_id: userId,
        question_slug: "variance_justification",
        inputs: VARIANCE_INPUTS,
      },
    },
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

test("a variance report grounds via get_adjacent_zoning and returns a definitive setback verdict", async ({
  request,
}) => {
  const userId = uniqueUser("adjzone");

  const created = await checkout(request, userId);
  expect(created.status).toBe("authorized");
  expect(created.question_slug).toBe("variance_justification");

  const answered = await runAnswer(request, created.purchase_id);

  // The turn grounded through get_adjacent_zoning (a GROUNDING_TOOL) and
  // captured — NOT voided as zero-evidence.
  expect(
    answered.status,
    `expected captured but got ${answered.status} ` +
      `(failure_reason=${answered.failure_reason}) — get_adjacent_zoning ` +
      "may be unregistered or missing from GROUNDING_TOOLS",
  ).toBe("captured");
  expect(answered.failure_reason).toBeNull();
  expect(answered.answer).toBeTruthy();

  // The answer is DEFINITIVE and grounded in the resolved abutting zone —
  // the shape a report should now produce instead of deferring to the
  // customer.
  expect(answered.answer).toContain("DH-1");
  expect(answered.answer.toLowerCase()).toContain("no variance is required");
  expect(answered.answer.toLowerCase()).not.toContain(
    "depends on adjacent zoning",
  );
});
