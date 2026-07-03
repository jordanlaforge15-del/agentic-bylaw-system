// Functional: ABS-343 — post-purchase report generation view.
//
// After free-start / checkout the answer engine runs as a real background
// job (advisor.billing: authorized → generating → captured). While the
// purchase sits in `generating`, the answer view plays a dedicated
// generation screen — the report's own letterhead + a six-step progress
// bar — and hands off to the finished deliverable only when a poll reports
// the job settled. The reveal is driven by the job state, not a fixed timer.
//
// Like abs321/abs331 this drives the real /app/answers view and the real
// proxy URLs, stubbing the priced-question proxy at the network boundary
// with page.route so the generating → ready sequence is deterministic.

import { expect, test } from "../fixtures/test-env";
import type { Page, Route } from "@playwright/test";

const GROUNDED_ANSWER =
  "Based on the bylaw evidence, a four-unit dwelling is permitted. " +
  "Source: RC-LUB §15.4(a).";

type Purchase = Record<string, unknown>;

function purchase(overrides: Purchase = {}): Purchase {
  return {
    id: 343,
    question_slug: "permitted_use",
    status: "captured",
    price_cents: 7900,
    currency: "CAD",
    answer: GROUNDED_ANSWER,
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

// Drive the proxy with a state machine rather than a fixed GET call
// sequence, so the assertions survive React StrictMode's dev double-mount
// (which fires an extra GET). `before` is served on GET until POST /answer
// fires; then `during` for one poll; then `after`. `answer` counts the
// engine runs so a test can prove the run did (or did NOT) happen.
async function stubGenerationMachine(
  page: Page,
  states: { before: Purchase; during: Purchase; after: Purchase },
  opts: { autoRun?: boolean } = {},
) {
  const counters = { answer: 0 };
  // When the engine is already running on arrival (leave-and-return), there
  // is no POST — settle after a couple of polls instead.
  const autoRun = opts.autoRun ?? true;
  let posted = false;
  let pollsAfterStart = 0;
  await page.route(
    /\/api\/billing\/questions\/purchases\/\d+(\/(answer|refine))?$/,
    (route) => {
      const url = route.request().url();
      const method = route.request().method();
      if (method === "POST" && url.endsWith("/answer")) {
        counters.answer += 1;
        posted = true;
        return json(route, states.during);
      }
      // GET.
      const started = autoRun ? posted : true;
      if (!started) return json(route, states.before);
      pollsAfterStart += 1;
      return json(route, pollsAfterStart <= 2 ? states.during : states.after);
    },
  );
  return counters;
}

test.describe("report generation view (ABS-343)", () => {
  test("an authorized purchase plays the six-step generation view then hands off to the report", async ({
    page,
  }) => {
    const counters = await stubGenerationMachine(page, {
      // Mount: card authorized, engine not yet run → the view fires POST.
      before: purchase({ status: "authorized", answer: null }),
      // The background job is running.
      during: purchase({ status: "generating", answer: null }),
      // The job settled — the report is ready.
      after: purchase({ status: "captured" }),
    });

    await page.goto("/app/answers/343");

    // The generation view: letterhead + progress bar + the six named steps
    // + the "saves to your case" note.
    await expect(page.getByTestId("report-generating")).toBeVisible({
      timeout: 8_000,
    });
    await expect(page.getByTestId("gen-progress")).toBeVisible();
    await expect(page.getByTestId("gen-step")).toHaveCount(6);
    await expect(page.getByTestId("gen-steps")).toContainText("Resolving parcel");
    await expect(page.getByTestId("gen-steps")).toContainText(
      "Verifying citations",
    );
    await expect(page.getByTestId("gen-note")).toContainText(
      /saves to your case/i,
    );

    // Hand-off: the finished deliverable replaces the generation view once
    // the poll reports `captured` (driven by the job, not a fixed timer).
    await expect(page.getByTestId("answer-body")).toContainText(
      /four-unit dwelling/i,
      { timeout: 15_000 },
    );
    await expect(page.getByTestId("report-generating")).toHaveCount(0);

    // The engine was fired exactly once (StrictMode double-mount guarded).
    expect(counters.answer).toBe(1);
  });

  test("returning to a still-generating report lands on the ready document without re-running the engine", async ({
    page,
  }) => {
    const counters = await stubGenerationMachine(
      page,
      {
        // The job is already running (the user left and came back) — no POST.
        before: purchase({ status: "generating", answer: null }),
        during: purchase({ status: "generating", answer: null }),
        // The job finishes server-side and the poll picks it up.
        after: purchase({ status: "captured" }),
      },
      { autoRun: false },
    );

    await page.goto("/app/answers/343");

    await expect(page.getByTestId("report-generating")).toBeVisible({
      timeout: 8_000,
    });
    await expect(page.getByTestId("answer-body")).toContainText(
      /four-unit dwelling/i,
      { timeout: 15_000 },
    );

    // The return path polls the existing job — it must NOT start a second
    // engine run ("it saves to the case").
    expect(counters.answer).toBe(0);
  });
});
