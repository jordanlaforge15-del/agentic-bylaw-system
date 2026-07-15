// Functional: ABS-376 — the $149 Development-standards report must ALWAYS
// evaluate use permission, before built-form standards, END TO END.
//
// The reopened bug:
//   DS-000020 (pre-ABS-375) led with the critical use-permission threshold:
//   multi-unit dwelling use in the INS zone is conditionally permitted only
//   on a Schedule 9 landmark site (Section 43) — the go/no-go question for
//   the whole proposal. After the ABS-375 prompt refocus (4074a6e, "Point
//   dev-standards + variance prompts at resolved data") the writer narrowed
//   to the resolved built-form facts and DS-000024 (identical repro input)
//   dropped use permission entirely — no use-permission section, no Schedule
//   9 reference, not even in "Unresolved items". A $149 answer that evaluates
//   *how* the building complies while silently skipping *whether* the use is
//   allowed is materially incomplete.
//
// The fix:
//   The dev-standards prompt (src/advisor/billing/questions.py) now REQUIRES
//   a "Use permission" section BEFORE the built-form standards, with an
//   explicit "cannot resolve — Schedule 9 absent from corpus" fallback.
//
// What this spec guards:
//   The flagship purchase runs end-to-end through the REAL buy-an-answer
//   service (checkout → authorize → run → settle) over the e2e FastAPI +
//   Postgres + MockGateway stack, and the built report the product surface
//   renders carries a use-permission block that LEADS the built-form table
//   and names Section 43 / Schedule 9 for multi-unit use in INS. The
//   MockGateway emits the exact use-permission-first shape the SKU now
//   produces (MOCK_DEV_STANDARDS_REPORT); the parser + prompt units live in
//   tests/advisor/billing/test_dev_standards_use_permission.py. Deleting the
//   use-permission section from the report turns this red.

import { expect, test } from "@playwright/test";

import { E2E_API_URL } from "../fixtures/test-env";

function uniqueUser(tag: string): string {
  return `abs376-${tag}-${Date.now()}-${Math.random()
    .toString(36)
    .slice(2, 8)}`;
}

// The ticket's repro input (ABS-360): the 1250 Robie St 4-storey / 14.5 m /
// 12-unit proposal. The MOCK_DEV_STANDARDS_REPORT sentinel rides the
// free-form project_details into the rendered prompt, steering the
// MockGateway to emit the use-permission-first report — no product code path
// is altered.
const DEV_STANDARDS_INPUTS = {
  address: "1250 Robie St, Halifax",
  project_details:
    "Proposed 4-storey multi-unit residential building: 14.5 m height, " +
    "12 units, front setback 3.0 m, rear setback 5.0 m, side setbacks " +
    "1.5 m each, lot coverage 48%, 8 parking spaces. MOCK_DEV_STANDARDS_REPORT",
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
        question_slug: "development_standards",
        inputs: DEV_STANDARDS_INPUTS,
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

test("the dev-standards report evaluates use permission before built-form standards (ABS-376)", async ({
  request,
}) => {
  const userId = uniqueUser("use-perm");

  const created = await checkout(request, userId);
  expect(created.status).toBe("authorized");
  expect(created.question_slug).toBe("development_standards");

  const answered = await runAnswer(request, created.purchase_id);
  expect(
    answered.status,
    `expected captured but got ${answered.status} ` +
      `(failure_reason=${answered.failure_reason})`,
  ).toBe("captured");

  const report = answered.report;
  expect(report, "build_report returned no report for a captured answer").toBeTruthy();

  const blocks: Array<{ type: string; title?: string }> = report.blocks;

  // A use-permission block exists — the section ABS-375 silently dropped.
  const useIdx = blocks.findIndex(
    (b) =>
      /use/i.test(b.title ?? "") && /permission/i.test(b.title ?? ""),
  );
  expect(
    useIdx,
    `dev-standards report is missing its use-permission block; blocks were ` +
      JSON.stringify(blocks.map((b) => ({ type: b.type, title: b.title }))),
  ).toBeGreaterThanOrEqual(0);

  // It LEADS the built-form standards — use permission is the go/no-go gate.
  const builtformIdx = blocks.findIndex(
    (b) => b.type === "table" && /built/i.test(b.title ?? ""),
  );
  expect(builtformIdx, "expected a built-form standards table").toBeGreaterThanOrEqual(
    0,
  );
  expect(
    useIdx,
    "use permission must be evaluated BEFORE built-form standards",
  ).toBeLessThan(builtformIdx);

  // The block names the governing threshold: Section 43 / Schedule 9 for
  // multi-unit use in INS — the DS-000020 finding DS-000024 dropped.
  const useBlob = JSON.stringify(blocks[useIdx]);
  expect(useBlob).toContain("Section 43");
  expect(useBlob).toContain("Schedule 9");
  expect(useBlob).toContain("INS");
});
