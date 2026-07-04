// Functional: ABS-354 — a purchase wedged in `generating` by a server
// restart settles to the failed / "wasn't charged" state on read instead
// of polling to the 2-minute timeout forever.
//
// ABS-343 runs report generation as an in-memory FastAPI BackgroundTasks
// job. If the server restarts (deploy, crash, dev reload) mid-run the task
// is lost and the purchase row is stranded in `generating` forever: every
// visit replays the six-step screen, polls 80×1.5s, and errors — refresh
// just repeats the cycle. The fix (advisor.billing.answers.
// settle_stale_generating, wired into the GET/POST purchase endpoints)
// force-settles a `generating` row untouched past the staleness cutoff to
// `failed`/`internal_error` and voids the Stripe hold, so the client
// settles to the no-charge error state and the user can retry.
//
// Like abs321/abs343/abs352 this drives the real /app/answers view and the
// real proxy URLs, stubbing the priced-question proxy at the network
// boundary so the rescue transition is deterministic. The GET stub models
// the fixed server contract: a stale `generating` row is settled to
// `failed` on read (the customer's hold voided → no charge).

import { expect, test } from "../fixtures/test-env";
import type { Page, Route } from "@playwright/test";

type Purchase = Record<string, unknown>;

function purchase(overrides: Purchase = {}): Purchase {
  return {
    id: 354,
    question_slug: "permitted_use",
    status: "failed",
    price_cents: 7900,
    currency: "CAD",
    answer: null,
    report: null,
    failure_reason: "internal_error",
    refinement_count: 0,
    refinements_remaining: 3,
    window_expires_at: null,
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

// Model the fixed server: the user arrives on a row wedged in `generating`
// (the first read still reports it as such), but the server's stale-row
// rescue force-settles it, so the very next poll reports `failed` with the
// hold voided. `answer` counts POST /answer calls to prove the wedged row
// is NEVER re-run through the engine (its status was `generating`, not
// `authorized`).
function stubStaleGeneratingRescue(page: Page) {
  const counters = { answer: 0 };
  let gets = 0;
  page.route(
    /\/api\/billing\/questions\/purchases\/\d+(\/(answer|refine))?$/,
    (route) => {
      const url = route.request().url();
      const method = route.request().method();
      if (method === "POST" && url.endsWith("/answer")) {
        counters.answer += 1;
        return json(route, purchase({ status: "generating" }));
      }
      // GET: the wedged `generating` row on arrival, then the rescued
      // `failed` state the server settles it to on read.
      gets += 1;
      return json(
        route,
        gets <= 1
          ? purchase({ status: "generating", failure_reason: null })
          : purchase({ status: "failed", failure_reason: "internal_error" }),
      );
    },
  );
  return counters;
}

test.describe("stale generating purchase rescue (ABS-354)", () => {
  test("a purchase wedged in generating settles to the no-charge failed state instead of polling forever", async ({
    page,
  }) => {
    const counters = stubStaleGeneratingRescue(page);

    await page.goto("/app/answers/354");

    // The rescue lands the view on the failed card — no answer, no charge —
    // well within the poll window (the fix settles on read; the old
    // behavior would spin to the ~2-minute "taking longer than expected"
    // timeout).
    await expect(page.getByTestId("answer-failed")).toBeVisible({
      timeout: 15_000,
    });
    await expect(page.getByTestId("answer-failed")).toContainText(
      /wasn't charged/i,
    );
    await expect(page.getByTestId("answer-failed")).toContainText(
      /You were\s+not charged/i,
    );

    // The six-step generation screen does not stay wedged on the page.
    await expect(page.getByTestId("report-generating")).toHaveCount(0);

    // A `generating` row is NOT `authorized`, so the view never fires a
    // second engine run while rescuing it.
    expect(counters.answer).toBe(0);
  });
});
