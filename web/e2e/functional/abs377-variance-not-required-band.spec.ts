// Functional: ABS-377 — the variance determination band reflects a
// "no variance required" conclusion instead of defaulting to a supportability
// verdict.
//
// The bug (VJ-000025, 5686 Spring Garden Rd, post-ABS-375):
//   The report's central finding was "VARIANCE MAY NOT BE REQUIRED" — the
//   resolved setback requirement was already met — yet the determination band
//   at the top read "PASS — Supportable on all three statutory tests". A
//   customer skimming the band got the OPPOSITE of the report's advice.
//
// Root cause:
//   build_report's status classifier had only pass/conditional/fail/attention
//   bands. A no-variance-required answer routinely also says the setback
//   "complies" / "PASSES" (a `pass` signal), so it resolved to `pass` →
//   "Supportable on all three statutory tests".
//
// The fix:
//   A distinct `not_required` band (chip "NOT REQUIRED", headline "Resolved
//   requirement already met — no variance needed") that wins over pass/fail
//   keywords, in src/advisor/billing/report.py + web/lib/report.ts.
//
// What this spec guards (the real repro, end-to-end):
//   The SAME real buy-answer flow the ABS-375 spec exercises — a variance
//   purchase whose answer definitively concludes "no variance is required" —
//   runs through the REAL run_answer + build_report over the e2e FastAPI +
//   Postgres + MockGateway stack, and the built report's verdict band is
//   `not_required`, NOT the misleading pass/supportable band.

import { E2E_API_URL, expect, test } from "../fixtures/test-env";
import type { Page, Route } from "@playwright/test";

function uniqueUser(tag: string): string {
  return `abs377-${tag}-${Date.now()}-${Math.random()
    .toString(36)
    .slice(2, 8)}`;
}

// A variance whose governing side-yard requirement resolves (via the abutting
// zone) such that no variance is required. The MOCK_ADJACENT_ZONING sentinel
// rides the free-text requested_variance slot so it reaches the dispatcher,
// which answers with "...the proposed 0.0 m side setback PASSES, and no
// variance is required." — the exact conclusion shape the band must reflect.
const VARIANCE_INPUTS = {
  address: "5686 Spring Garden Rd, Halifax",
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

test("a no-variance-required conclusion renders the NOT REQUIRED band, not PASS/Supportable", async ({
  request,
}) => {
  const userId = uniqueUser("notreq");

  const created = await checkout(request, userId);
  expect(created.status).toBe("authorized");
  expect(created.question_slug).toBe("variance_justification");

  const answered = await runAnswer(request, created.purchase_id);
  expect(answered.status).toBe("captured");
  expect(answered.answer.toLowerCase()).toContain("no variance is required");

  // The built determination band reflects the report's conclusion: the
  // resolved requirement is already met, so no variance is required. It does
  // NOT default to the supportability verdict, even though the answer also
  // says the setback "PASSES".
  const verdict = answered.report.verdict;
  expect(verdict.status).toBe("not_required");
  expect(verdict.label.toLowerCase()).toContain("no variance needed");
  expect(verdict.label).not.toContain("Supportable on all three statutory tests");
});

// The rendered determination band on the report document — guards the client
// statusInfo() mapping for the new `not_required` band (web/lib/report.ts).
function json(route: Route, body: unknown, status = 200) {
  return route.fulfill({
    status,
    contentType: "application/json",
    body: JSON.stringify(body),
  });
}

test("the report document renders a NOT REQUIRED chip for a not-required verdict", async ({
  page,
}: {
  page: Page;
}) => {
  const report = {
    ref: "VJ-000377",
    report_type: "Variance justification package",
    address: "5686 Spring Garden Rd",
    zone_subtitle: "Established Residential, Type 1",
    issued: "2026-07-15",
    prepared_for: "Jordan Buyer",
    bylaw_version: "HRM Regional Centre Land Use By-law — 2024 consolidation",
    price_cents: 29900,
    currency: "CAD",
    verdict: {
      status: "not_required",
      label: "Resolved requirement already met — no variance needed",
    },
    summary:
      "The resolved side-yard requirement is already met, so no variance is " +
      "required at 5686 Spring Garden Rd.",
    blocks: [
      {
        type: "finding",
        status: "not_required",
        title: "Statutory tests",
        body: "The requirement resolves to 0.0 m; no variance is required.",
      },
    ],
    footer:
      "This report was generated by ABS° and grounded in the HRM Regional " +
      "Centre Land Use By-law — 2024 consolidation. Reference VJ-000377.",
  };

  await page.route(
    /\/api\/billing\/questions\/purchases\/\d+(\/(answer|refine))?$/,
    (route) =>
      json(route, {
        id: 377,
        question_slug: "variance_justification",
        status: "captured",
        price_cents: 29900,
        currency: "CAD",
        answer: "Raw markdown fallback.",
        report,
        failure_reason: null,
        refinement_count: 0,
        refinements_remaining: 3,
        window_expires_at: "2026-12-31T00:00:00Z",
      }),
  );

  await page.goto("/app/answers/377");

  await expect(page.getByTestId("report-document")).toBeVisible();
  await expect(page.getByTestId("report-verdict-chip")).toHaveText(
    "NOT REQUIRED",
  );
  const band = page.getByTestId("report-verdict");
  await expect(band).toContainText("no variance needed");
  await expect(band).not.toContainText(/PASS/);
  await expect(band).not.toContainText(
    /Supportable on all three statutory tests/,
  );
});
