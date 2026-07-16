// Functional: ABS-345 case-aware sidebar — REPORT badge + address/zone
// rows so priced "report" purchases (Answers product) and conversations
// (chat sessions) coexist legibly in one list.
//
// The sidebar merges two sources: GET /api/chat/sessions (conversations)
// and GET /api/billing/questions/purchases (reports). The reports list
// endpoint lives on the billing router, which is dormant in the
// payments-off e2e stack (same situation ABS-321 handles for the answer
// view), so this spec stubs both proxy responses at the network boundary
// with page.route and asserts the rendered rows. It verifies:
//   - a report-backed row renders the accent REPORT badge;
//   - rows show the anchor address (bold) + zone;
//   - an unopened report shows the unread accent dot;
//   - a conversation row carries neither badge;
//   - a settled-failed report (status `failed` or `voided`) shows a
//     distinct FAILED tag instead of the generating pill / unread dot,
//     so it's spottable in the list without opening it (ABS-367).

import { expect, test } from "../fixtures/test-env";
import type { Page, Route } from "@playwright/test";

function json(route: Route, body: unknown, status = 200) {
  return route.fulfill({
    status,
    contentType: "application/json",
    body: JSON.stringify(body),
  });
}

const CONVERSATION = {
  session_id: "sess-1",
  model: "claude-opus-4-5",
  title: "1208 Robie St · Maximum height",
  message_count: 4,
  updated_at: "2026-07-02T12:00:00Z",
  case_id: 11,
  anchor_label: "1208 Robie St",
  anchor_kind: "address",
  zone: "COR",
  question: "Maximum height + bonusing",
  kind: "conversation",
};

const REPORT = {
  id: 900,
  question_slug: "permitted_use",
  title: "Permitted-use check",
  status: "captured",
  address: "5184 Morris St",
  zone: "ER-1",
  answer_ready: true,
  updated_at: "2026-07-03T09:00:00Z",
};

async function stubSidebar(
  page: Page,
  opts: { sessions?: unknown[]; reports?: unknown[] } = {},
) {
  const sessions = opts.sessions ?? [CONVERSATION];
  const reports = opts.reports ?? [REPORT];
  await page.route(/\/api\/chat\/sessions(\?.*)?$/, (route) =>
    json(route, { sessions }),
  );
  await page.route(/\/api\/billing\/questions\/purchases$/, (route) =>
    json(route, { reports }),
  );
}

test.describe("case-aware sidebar (ABS-345)", () => {
  test("a report-backed row renders the REPORT badge with address + zone", async ({
    page,
  }) => {
    await stubSidebar(page);
    await page.goto("/app");

    const sidebar = page.locator("aside").first();
    const reportRow = sidebar.locator('[data-testid="case-row"][data-kind="report"]');
    await expect(reportRow).toBeVisible({ timeout: 8_000 });

    // REPORT badge appears on the report row.
    await expect(
      reportRow.getByTestId("report-badge"),
    ).toHaveText(/report/i);

    // Row shows the anchor address (bold) + zone.
    await expect(reportRow).toContainText("5184 Morris St");
    await expect(reportRow).toContainText("ER-1");

    // An unopened, answer-ready report shows the unread dot.
    await expect(reportRow.getByTestId("unread-dot")).toBeVisible();
  });

  test("a still-generating report row shows the generating affordance, not an unread dot (ABS-343)", async ({
    page,
  }) => {
    await stubSidebar(page, {
      reports: [
        {
          ...REPORT,
          id: 901,
          status: "generating",
          answer_ready: false,
        },
      ],
    });
    await page.goto("/app");

    const sidebar = page.locator("aside").first();
    const reportRow = sidebar.locator(
      '[data-testid="case-row"][data-kind="report"]',
    );
    await expect(reportRow).toBeVisible({ timeout: 8_000 });

    // The case-list item reflects the in-flight generation job.
    await expect(reportRow.getByTestId("row-generating")).toContainText(
      /generating/i,
    );
    // A row that is still generating has no deliverable yet → no unread dot.
    await expect(reportRow.getByTestId("unread-dot")).toHaveCount(0);
    // It's still a report row (badge unchanged).
    await expect(reportRow.getByTestId("report-badge")).toHaveText(/report/i);
  });

  test.describe("a failed report shows a FAILED tag instead of the generating pill / unread dot (ABS-367)", () => {
    for (const status of ["failed", "voided"] as const) {
      test(`status=${status}`, async ({ page }) => {
        await stubSidebar(page, {
          reports: [
            {
              ...REPORT,
              id: 902,
              status,
              answer_ready: false,
            },
          ],
        });
        await page.goto("/app");

        const sidebar = page.locator("aside").first();
        const reportRow = sidebar.locator(
          '[data-testid="case-row"][data-kind="report"]',
        );
        await expect(reportRow).toBeVisible({ timeout: 8_000 });

        // The case-list item carries a distinct FAILED tag — spottable
        // without opening the report.
        await expect(reportRow.getByTestId("row-failed")).toContainText(
          /failed/i,
        );
        // No generating pill and no unread dot on a settled-failed row.
        await expect(reportRow.getByTestId("row-generating")).toHaveCount(0);
        await expect(reportRow.getByTestId("unread-dot")).toHaveCount(0);
        // It's still a report row (REPORT badge unchanged).
        await expect(reportRow.getByTestId("report-badge")).toHaveText(
          /report/i,
        );
      });
    }
  });

  test("a conversation row carries no REPORT badge", async ({ page }) => {
    await stubSidebar(page);
    await page.goto("/app");

    const sidebar = page.locator("aside").first();
    const convoRow = sidebar.locator(
      '[data-testid="case-row"][data-kind="conversation"]',
    );
    await expect(convoRow).toBeVisible({ timeout: 8_000 });
    await expect(convoRow).toContainText("1208 Robie St");
    await expect(convoRow).toContainText("COR");
    await expect(convoRow.getByTestId("report-badge")).toHaveCount(0);
  });

  test("opening a report row clears its unread dot", async ({ page }) => {
    await stubSidebar(page);
    // Stub the by-id answer fetch so the in-canvas report view renders after
    // nav into the unified /app workspace (ABS-344).
    await page.route(/\/api\/billing\/questions\/purchases\/\d+$/, (route) =>
      json(route, {
        id: 900,
        question_slug: "permitted_use",
        status: "captured",
        price_cents: 7900,
        currency: "CAD",
        answer: "Based on the bylaw evidence, the use is permitted.",
        failure_reason: null,
        refinement_count: 0,
        refinements_remaining: 3,
        window_expires_at: "2026-12-31T00:00:00Z",
      }),
    );
    await page.goto("/app");

    const sidebar = page.locator("aside").first();
    const reportRow = sidebar.locator(
      '[data-testid="case-row"][data-kind="report"]',
    );
    await expect(reportRow.getByTestId("unread-dot")).toBeVisible();

    await reportRow.click();
    await expect(page).toHaveURL(/\/app\?report_id=900/, { timeout: 8_000 });

    // Navigate back to /app — the row must now be marked seen (no dot).
    await page.goto("/app");
    const reportRow2 = sidebar.locator(
      '[data-testid="case-row"][data-kind="report"]',
    );
    await expect(reportRow2).toBeVisible({ timeout: 8_000 });
    await expect(reportRow2.getByTestId("unread-dot")).toHaveCount(0);
  });
});
