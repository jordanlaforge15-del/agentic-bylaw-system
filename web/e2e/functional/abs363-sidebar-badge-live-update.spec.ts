// Functional: ABS-363 — case-list GENERATING badge clears live, no reload.
//
// Repro: open a case, buy a report, watch it generate in the center pane.
// Once the job settles the center pane swaps to the finished document (as
// covered by ABS-343/344's specs) but the sidebar's own row for that same
// case kept showing `REPORT · GENERATING` until a full page reload — the
// sidebar only refetches its list on `refreshTrigger` bumps, and nothing
// bumped it when AnswerView's poll (inside the report canvas) detected the
// job had settled.
//
// This spec drives the real /app workspace with a report opened via
// ?report_id=, stubbing both the purchase-detail proxy (what AnswerView
// polls) and the sidebar's reports-list proxy so both surfaces observe the
// same "settled" transition, then asserts the sidebar's own row for that
// report flips from the generating pill to the plain REPORT badge without
// any navigation or reload.

import { expect, test } from "../fixtures/test-env";
import type { Page, Route } from "@playwright/test";

type Purchase = Record<string, unknown>;

function purchase(overrides: Purchase = {}): Purchase {
  return {
    id: 363,
    question_slug: "due_diligence",
    status: "generating",
    price_cents: 19900,
    currency: "CAD",
    answer: null,
    report: null,
    failure_reason: null,
    refinement_count: 0,
    refinements_remaining: 3,
    window_expires_at: "2026-12-31T00:00:00Z",
    ...overrides,
  };
}

function json(route: Route, body: unknown, status = 200) {
  return route.fulfill({
    status,
    contentType: "application/json",
    body: JSON.stringify(body),
  });
}

// A single shared "settled" flag drives both proxies so the sidebar's list
// row and the center-pane detail poll agree on the job's state, mirroring
// how the real backend would report the same purchase from two endpoints.
async function stubWorkspace(page: Page) {
  const state = { settled: false, pollsSinceStart: 0 };

  await page.route(
    /\/api\/billing\/questions\/purchases\/\d+(\/answer)?$/,
    (route) => {
      if (route.request().method() === "POST") {
        // Engine already "running" server-side — no separate authorized step
        // needed for this spec, so classify() lands straight on generating.
        return json(route, purchase({ status: "generating" }));
      }
      // GET (the poll). Settle after a couple of polls so the generating
      // view is observably on-screen before the hand-off.
      state.pollsSinceStart += 1;
      if (state.pollsSinceStart > 2) state.settled = true;
      return json(
        route,
        state.settled
          ? purchase({
              status: "captured",
              answer: "Based on the bylaw evidence, this use is permitted.",
            })
          : purchase({ status: "generating" }),
      );
    },
  );

  await page.route(/\/api\/billing\/questions\/purchases$/, (route) =>
    json(route, {
      reports: [
        {
          id: 363,
          question_slug: "due_diligence",
          title: "Zoning due-diligence summary",
          status: state.settled ? "captured" : "generating",
          address: "1234 Elm Street",
          zone: "ER-1",
          answer_ready: state.settled,
          updated_at: "2026-07-06T12:00:00Z",
        },
      ],
    }),
  );

  await page.route(/\/api\/chat\/sessions(\?.*)?$/, (route) =>
    json(route, { sessions: [] }),
  );

  return state;
}

test.describe("sidebar GENERATING badge clears live on completion (ABS-363)", () => {
  test("the sidebar's own row flips generating → ready without a reload", async ({
    page,
  }) => {
    await stubWorkspace(page);
    await page.goto("/app?report_id=363");

    const sidebar = page.locator("aside").first();
    const reportRow = sidebar.locator(
      '[data-testid="case-row"][data-kind="report"]',
    );
    await expect(reportRow).toBeVisible({ timeout: 8_000 });

    // Center pane shows the generation view; the sidebar row for the SAME
    // report shows the generating pill, not a plain REPORT badge alone.
    await expect(page.getByTestId("workspace-label")).toContainText(
      "GENERATING",
    );
    await expect(reportRow.getByTestId("row-generating")).toContainText(
      /generating/i,
    );

    // The job settles — the center pane hands off to the finished report.
    await expect(page.getByTestId("workspace-label")).toContainText(
      "REPORT",
      { timeout: 15_000 },
    );

    // The sidebar row for the SAME case must flip live too — no reload,
    // no re-navigation. This is the ABS-363 regression: previously the
    // generating pill kept showing here even after the center pane settled.
    await expect(reportRow.getByTestId("row-generating")).toHaveCount(0, {
      timeout: 8_000,
    });
    await expect(reportRow.getByTestId("report-badge")).toHaveText(/report/i);
  });
});
