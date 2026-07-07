// Functional: ABS-359 — the report deliverable must never carry the agent's
// chain-of-thought, END TO END through the REAL parser.
//
// The bug (and why the first fix's spec missed it):
//   The engine's markdown opens with a first-person planning monologue
//   ("I have gathered the key provisions … Let me compile the findings:")
//   fenced off from the report by a `---` rule, sprinkles decorative `---`
//   between sections, and tacks a conversational sign-off ("I apologize that
//   I cannot provide … Would you like me to research a different property?")
//   onto the final section. build_report must scrub ALL of it.
//
//   The sibling spec (abs359-report-clean-summary.spec.ts) stubs an
//   ALREADY-CLEAN report at the network boundary, so it guards the
//   ReportDocument RENDERING but cannot catch a parser regression — a
//   monologue leak sails straight through it (the re-test that reopened this
//   ticket). This spec closes that gap: it runs the REAL buy-an-answer
//   service (checkout → authorize → run → settle) over the e2e FastAPI +
//   Postgres + MockGateway stack, so the answer text is produced by the
//   engine loop and turned into a report by the REAL
//   advisor.billing.report.parse_blocks. The MockGateway emits the exact
//   monologue shape the real engine produces (MOCK_MONOLOGUE_REPORT), and we
//   assert the built report the product surface renders is clean.
//
//   Reverting the parser fix (src/advisor/billing/report.py) turns this red.

import { expect, test } from "@playwright/test";

import { E2E_API_URL } from "../fixtures/test-env";

function uniqueUser(tag: string): string {
  return `abs359-${tag}-${Date.now()}-${Math.random()
    .toString(36)
    .slice(2, 8)}`;
}

// A routine variance package. The MOCK_MONOLOGUE_REPORT sentinel rides in the
// free-form hardship_rationale, which the prompt_template renders into the
// engine prompt — steering the MockGateway to emit a monologue-laden answer
// without changing any product code path.
const VARIANCE_INPUTS = {
  address: "5686 Spring Garden Rd, Halifax",
  requested_variance: "Rear-yard setback reduced from 6.0 m to 5.6 m.",
  hardship_rationale:
    "Irregular lot depth constrains the buildable envelope. MOCK_MONOLOGUE_REPORT",
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

test("the built report scrubs agent monologue, sign-off, and stray rules (ABS-359)", async ({
  request,
}) => {
  const userId = uniqueUser("parser");

  const created = await checkout(request, userId);
  expect(created.status).toBe("authorized");

  const answered = await runAnswer(request, created.purchase_id);
  expect(
    answered.status,
    `expected captured but got ${answered.status} ` +
      `(failure_reason=${answered.failure_reason})`,
  ).toBe("captured");

  // The raw captured answer DID carry the monologue — proving the mock drove
  // the real chain-of-thought shape, not a pre-cleaned fixture.
  expect(answered.answer).toContain("Let me compile the findings");
  expect(answered.answer).toContain("I apologize that I cannot provide");

  // The built report — the thing the product surface renders — is clean.
  const report = answered.report;
  expect(report, "build_report returned no report for a captured answer").toBeTruthy();

  // Summary is the report's own lead, not the planning monologue.
  expect(report.summary).toBe(
    "The requested rear-yard variance is supportable on all three statutory tests.",
  );

  // No chain-of-thought, sign-off, or stray horizontal rule survives ANYWHERE
  // in the deliverable (summary + every block).
  const serialized = JSON.stringify(report);
  for (const forbidden of [
    "Let me compile",
    "I have gathered",
    "sufficient information",
    "I apologize",
    "Would you like me to",
    "---",
  ]) {
    expect(
      serialized,
      `report leaks agent chatter/scaffolding: "${forbidden}"`,
    ).not.toContain(forbidden);
  }

  // The report still carries its real content — the fix strips chatter, it
  // does not gut the deliverable.
  expect(serialized).toContain("statutory");
});
