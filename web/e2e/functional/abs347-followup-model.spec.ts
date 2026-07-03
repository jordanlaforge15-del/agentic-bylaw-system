// ABS-347 — the follow-up model as an executable contract.
//
// Decision (docs/decisions/2026-07-followup-model-conversation-continuation.md):
// the follow-up path is GROUNDED CONVERSATION CONTINUATION, metered by the
// bounded refinement window. The conversation is the UX; the window is the
// meter. This spec asserts the two defining properties of that decision on the
// live follow-up surface (the /app/answers refinement composer, which enforces
// the identical contract until ABS-344 moves it into the workspace):
//
//   1. CONTINUATION IS GROUNDED — an in-window follow-up continues the SAME
//      grounded answer: the original citation survives into the refined answer,
//      rather than the follow-up producing a fresh, ungrounded reply.
//   2. THE WINDOW IS THE BOUNDARY — a materially-new question is not served free
//      (routed to a new purchase), and an exhausted window dead-ends to buying a
//      new answer. This is what keeps the per-question price anchored (ABS-312).
//
// The backend answer/refine endpoints are dormant in the e2e stack (payments
// off, ABS-322), so — like abs321 — this drives the real view + proxy URLs and
// stubs the proxy responses at the network boundary. It is intentionally scoped
// to the follow-up CONTRACT, not the workspace UI (that is ABS-344).

import { expect, test } from "../fixtures/test-env";
import type { Page, Route } from "@playwright/test";

const CITATION = "RC-LUB §15.4(a)";
const GROUNDED_ANSWER =
  "Based on the bylaw evidence, a four-unit dwelling is permitted. " +
  `Source: ${CITATION}.`;

type Purchase = {
  id: number;
  question_slug: string;
  status: string;
  price_cents: number;
  currency: string;
  answer: string | null;
  failure_reason: string | null;
  refinement_count: number;
  refinements_remaining: number;
  window_expires_at: string | null;
};

function purchase(overrides: Partial<Purchase> = {}): Purchase {
  return {
    id: 347,
    question_slug: "permitted_use",
    status: "captured",
    price_cents: 7900,
    currency: "CAD",
    answer: GROUNDED_ANSWER,
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

async function stubPurchaseApi(
  page: Page,
  handlers: {
    get: () => unknown;
    refine?: (route: Route) => unknown;
  },
) {
  await page.route(
    /\/api\/billing\/questions\/purchases\/\d+(\/(answer|refine))?$/,
    (route) => {
      const url = route.request().url();
      const method = route.request().method();
      if (method === "POST" && url.endsWith("/refine")) {
        return handlers.refine
          ? handlers.refine(route)
          : json(route, handlers.get());
      }
      return json(route, handlers.get());
    },
  );
}

test.describe("follow-up model — grounded continuation, metered window (ABS-347)", () => {
  test("property 1: an in-window follow-up continues the SAME grounded answer (citation survives)", async ({
    page,
  }) => {
    // The refined answer re-states the follow-up's ask but carries the
    // ORIGINAL determination's citation forward — the follow-up is a
    // continuation of the grounded answer, not a fresh ungrounded reply.
    await stubPurchaseApi(page, {
      get: () => purchase(),
      refine: (route) =>
        json(
          route,
          purchase({
            answer:
              "In three bullets: (1) four units permitted, " +
              "(2) lot must be ≥ 400 m², " +
              `(3) still grounded in ${CITATION}.`,
            refinement_count: 1,
            refinements_remaining: 2,
          }),
        ),
    });
    await page.goto("/app/answers/347");

    // The first answer is grounded.
    await expect(page.getByTestId("answer-body")).toContainText(CITATION);

    // A clarifying follow-up (a reformat — NOT a new subject) is served free.
    await page
      .getByTestId("refine-input")
      .fill("Summarize the same determination in three bullets.");
    await page.getByTestId("refine-submit").click();

    // Continuation is grounded: the original citation is carried into the
    // refined answer, and one free follow-up has been consumed.
    const body = page.getByTestId("answer-body");
    await expect(body).toContainText(/three bullets/i);
    await expect(body).toContainText(CITATION);
    await expect(page.getByTestId("refine-remaining")).toContainText(
      /2 FOLLOW-UPS LEFT/i,
    );
  });

  test("property 2a: a materially-new question is NOT served free — routed to a new purchase", async ({
    page,
  }) => {
    await stubPurchaseApi(page, {
      get: () => purchase(),
      refine: (route) =>
        json(
          route,
          {
            detail: {
              code: "new_question",
              message:
                "This is a different question — please purchase a new answer.",
              suggested_slug: "permitted_use",
            },
          },
          409,
        ),
    });
    await page.goto("/app/answers/347");
    await page
      .getByTestId("refine-input")
      .fill("What about 999 Oak Avenue for a commercial use instead?");
    await page.getByTestId("refine-submit").click();

    // The window boundary holds: the new subject is refused inline and the
    // user is routed to the question menu (protecting the per-question price).
    const notice = page.getByTestId("refine-notice");
    await expect(notice).toContainText(/different question/i);
    await expect(notice).toContainText(/question menu/i);
  });

  test("property 2b: an exhausted window dead-ends to buying a new answer", async ({
    page,
  }) => {
    await stubPurchaseApi(page, {
      get: () => purchase(),
      refine: (route) =>
        json(
          route,
          {
            detail: {
              code: "window_exhausted",
              message:
                "The refinement window for this answer is closed — please " +
                "purchase a new answer.",
              reason: "refinements_exhausted",
            },
          },
          409,
        ),
    });
    await page.goto("/app/answers/347");
    await page
      .getByTestId("refine-input")
      .fill("One more clarification on the same determination, please.");
    await page.getByTestId("refine-submit").click();

    // The metered budget is spent: the free follow-up path closes and the
    // only way forward is a new purchase.
    await expect(page.getByTestId("refine-notice")).toContainText(
      /window closed/i,
    );
    await expect(page.getByTestId("refine-closed")).toContainText(
      /buy a new answer/i,
    );
    await expect(page.getByTestId("refine-input")).toHaveCount(0);
  });
});
