// Functional: ABS-374 — a report section that announces a list ("The complete
// list is:") but delivers no items must not leave the dangling colon sentence
// in the paid deliverable, END TO END through the REAL parser.
//
// The bug (report-SKU retest, PU-000019 / VJ-000022):
//   The engine wrote a preamble promising an enumeration ("The complete list
//   is:", "Key uncertainties include:") and then emitted no list — the items
//   never came. The colon sentence dangled straight into the next section, so
//   a $79/$299 document read "The complete list is:" immediately followed by
//   the conclusion, with no list between.
//
//   build_report (advisor.billing.report.parse_blocks) must DROP the dangling
//   announcement while keeping the real prose either side. This spec runs the
//   REAL buy-an-answer service (checkout → authorize → run → settle) over the
//   e2e FastAPI + Postgres + MockGateway stack, so the answer text is produced
//   by the engine loop and turned into a report by the REAL parser. The
//   MockGateway emits the exact PU-000019 shape (MOCK_DANGLING_LIST_REPORT).
//
//   Reverting the parser fix (src/advisor/billing/report.py) turns this red.

import { expect, test } from "@playwright/test";

import { E2E_API_URL } from "../fixtures/test-env";

function uniqueUser(tag: string): string {
  return `abs374-${tag}-${Date.now()}-${Math.random()
    .toString(36)
    .slice(2, 8)}`;
}

// A routine permitted-use check. The MOCK_DANGLING_LIST_REPORT sentinel rides
// in the free-form proposed_use, which the prompt_template renders into the
// engine prompt — steering the MockGateway to emit a list-announcing section
// with no items, without changing any product code path.
const PERMITTED_USE_INPUTS = {
  address: "5184 Morris St, Halifax",
  proposed_use:
    "a home-based child daycare. MOCK_DANGLING_LIST_REPORT",
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
        question_slug: "permitted_use",
        inputs: PERMITTED_USE_INPUTS,
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

test("the built report drops a dangling list-announcing preamble (ABS-374)", async ({
  request,
}) => {
  const userId = uniqueUser("danglist");

  const created = await checkout(request, userId);
  expect(created.status).toBe("authorized");

  const answered = await runAnswer(request, created.purchase_id);
  expect(
    answered.status,
    `expected captured but got ${answered.status} ` +
      `(failure_reason=${answered.failure_reason})`,
  ).toBe("captured");

  // The raw captured answer DID carry the dangling announcement — proving the
  // mock drove the real broken shape, not a pre-cleaned fixture.
  expect(answered.answer).toContain("The complete list is:");

  // The built report — the thing the product surface renders — has no dangling
  // colon announcement anywhere (summary + every block).
  const report = answered.report;
  expect(report, "build_report returned no report for a captured answer").toBeTruthy();

  const serialized = JSON.stringify(report);
  expect(
    serialized,
    "report leaks the dangling list announcement",
  ).not.toContain("The complete list is:");
  expect(serialized).not.toContain("complete list is");

  // No block ends on a bare colon — the DoD invariant: no colon-terminated
  // list preamble followed directly by a heading or new section.
  for (const block of report.blocks ?? []) {
    const text: string = (block.text ?? block.body ?? "").trim();
    expect(
      text.endsWith(":"),
      `block "${block.title ?? block.type}" dangles on a colon: ${text}`,
    ).toBeFalsy();
  }
  expect((report.summary ?? "").trim().endsWith(":")).toBeFalsy();

  // The fix strips only the dangling announcement — the real content either
  // side survives.
  expect(serialized).toContain("Section 51 lists the permitted home occupation uses");
  expect(serialized).toContain("Child daycare is not listed");
});
