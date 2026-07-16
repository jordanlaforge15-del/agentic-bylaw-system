// Functional: ABS-352 — the stale `ranAnswer` guard suppressed POST
// /answer for a second authorized report opened in the same session.
//
// AnswerView (web/components/product/answer-view.tsx) fires POST /answer
// once per mounted instance via a `useRef` guard, but chat-shell.tsx
// rendered <AnswerView> without a `key`, and the sidebar switches reports
// via `router.push('/app?report_id=N')` — a same-route client nav that
// does NOT remount the component. So switching from report A to report B
// reused A's instance (and its already-tripped ref): B's POST never
// fired, the view showed the six-step generating screen forever, and
// the purchase stayed `authorized` in the DB.
//
// The fix adds `key={reportIdFromUrl}` to <AnswerView> in chat-shell.tsx
// so each report id gets a fresh mount (fresh ref). This spec drives the
// real /app workspace + sidebar (not page.goto per report, which would
// mask the bug by remounting the whole page) and stubs only the purchase
// proxy + sidebar list endpoints at the network boundary.

import { expect, test } from "../fixtures/test-env";
import type { Page, Route } from "@playwright/test";

type Purchase = Record<string, unknown>;

function purchase(id: number, overrides: Purchase = {}): Purchase {
  return {
    id,
    question_slug: "due_diligence",
    status: "captured",
    price_cents: 19900,
    currency: "CAD",
    answer: `Grounded answer for report ${id}.`,
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

// Per-id state machine: GET returns `authorized` until POST /answer fires
// for that id, then `generating` for a couple of polls, then `captured`.
// Tracks POST counts per id so the test can prove each report's engine
// ran exactly once — the exact thing the stale ref suppressed for the
// second report.
function stubPerReportAnswerMachine(page: Page) {
  const posted = new Map<number, boolean>();
  const pollsAfterPost = new Map<number, number>();
  const postCounts = new Map<number, number>();

  page.route(
    /\/api\/billing\/questions\/purchases\/(\d+)(\/(answer|refine))?$/,
    (route) => {
      const url = route.request().url();
      const method = route.request().method();
      const match = url.match(/purchases\/(\d+)/);
      const id = Number(match?.[1]);

      if (method === "POST" && url.endsWith("/answer")) {
        posted.set(id, true);
        postCounts.set(id, (postCounts.get(id) ?? 0) + 1);
        return json(route, purchase(id, { status: "generating", answer: null }));
      }

      // GET.
      if (!posted.get(id)) {
        return json(route, purchase(id, { status: "authorized", answer: null }));
      }
      const polls = (pollsAfterPost.get(id) ?? 0) + 1;
      pollsAfterPost.set(id, polls);
      if (polls <= 2) {
        return json(route, purchase(id, { status: "generating", answer: null }));
      }
      return json(route, purchase(id, { status: "captured" }));
    },
  );

  return { postCounts };
}

async function stubSidebar(page: Page, reports: unknown[]) {
  await page.route(/\/api\/chat\/sessions(\?.*)?$/, (route) =>
    json(route, { sessions: [] }),
  );
  await page.route(/\/api\/billing\/questions\/purchases$/, (route) =>
    json(route, { reports }),
  );
}

const REPORT_A = {
  id: 5201,
  question_slug: "due_diligence",
  title: "Due diligence report",
  status: "authorized",
  address: "1967 Woodlawn Terrace",
  zone: "R-1",
  answer_ready: false,
  updated_at: "2026-07-04T09:00:00Z",
};

const REPORT_B = {
  id: 5202,
  question_slug: "permitted_use",
  title: "Permitted-use check",
  status: "authorized",
  address: "42 Spring Garden Road",
  zone: "COR",
  answer_ready: false,
  updated_at: "2026-07-04T09:05:00Z",
};

test.describe("second authorized report never generates (ABS-352)", () => {
  test("opening two authorized reports back-to-back via the sidebar both fire POST /answer and reach ready", async ({
    page,
  }) => {
    const { postCounts } = stubPerReportAnswerMachine(page);
    await stubSidebar(page, [REPORT_A, REPORT_B]);

    await page.goto("/app");

    const sidebar = page.locator("aside").first();

    // Open report A first.
    await sidebar.getByText("1967 Woodlawn Terrace").click();
    await expect(page).toHaveURL(/report_id=5201/);
    await expect(page.getByTestId("report-generating")).toBeVisible({
      timeout: 8_000,
    });
    await expect(page.getByTestId("answer-body")).toContainText(
      /Grounded answer for report 5201/i,
      { timeout: 15_000 },
    );
    expect(postCounts.get(5201)).toBe(1);

    // Switch to report B via the sidebar — a same-route client nav, NOT a
    // full page reload. Without `key={reportIdFromUrl}` on <AnswerView>,
    // the component instance (and its tripped `ranAnswer` ref) survives
    // and B's POST never fires.
    await sidebar.getByText("42 Spring Garden Road").click();
    await expect(page).toHaveURL(/report_id=5202/);
    await expect(page.getByTestId("report-generating")).toBeVisible({
      timeout: 8_000,
    });
    await expect(page.getByTestId("answer-body")).toContainText(
      /Grounded answer for report 5202/i,
      { timeout: 15_000 },
    );
    expect(postCounts.get(5202)).toBe(1);
  });

  test("reloading a settled (captured) report renders it without any POST /answer calls", async ({
    page,
  }) => {
    let postCount = 0;
    await page.route(
      /\/api\/billing\/questions\/purchases\/\d+(\/(answer|refine))?$/,
      (route) => {
        const url = route.request().url();
        const method = route.request().method();
        if (method === "POST" && url.endsWith("/answer")) {
          postCount += 1;
          return json(route, purchase(5203, { status: "captured" }));
        }
        return json(route, purchase(5203, { status: "captured" }));
      },
    );

    await page.goto("/app?report_id=5203");

    await expect(page.getByTestId("answer-body")).toContainText(
      /Grounded answer for report 5203/i,
      { timeout: 15_000 },
    );
    expect(postCount).toBe(0);
  });
});
