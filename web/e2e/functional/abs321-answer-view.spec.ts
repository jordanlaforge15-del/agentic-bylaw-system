// Functional: ABS-321 post-purchase answer delivery UI.
//
// Covers the /app/answers/[id] surface a user lands on after buying (or,
// payments-off, spending a free credit on) a priced question:
//   - an already-captured purchase renders its grounded answer WITH the
//     ABS-313 on-output disclaimer;
//   - an "authorized" (not-yet-run) purchase auto-runs the answer
//     (POST .../answer) and then renders it;
//   - an in-window refinement updates the answer and decrements the
//     remaining-follow-ups counter;
//   - the ABS-317 guardrails route inline: a materially new question and
//     an exhausted window both dead-end to "buy a new answer" instead of
//     being served free;
//   - a failed (voided) question shows the no-charge explanation.
//
// The backend priced-question answer/refine endpoints are only mounted
// when billing is ENABLED (live Stripe). In the e2e stack billing is
// dormant (payments-off) and that wiring is ABS-322's scope, so this
// spec drives the real /app/answers view and the real proxy URLs while
// stubbing the proxy responses at the network boundary with page.route.
// It exercises every branch of the view's fetch/answer/refine logic and
// the 409 guardrail routing — the part ABS-321 owns.

import { expect, test } from "../fixtures/test-env";
import type { Page, Route } from "@playwright/test";

const GROUNDED_ANSWER =
  "Based on the bylaw evidence, a four-unit dwelling is permitted. " +
  "Source: RC-LUB §15.4(a).";

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
    id: 321,
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

/**
 * Install a router for the priced-question proxy endpoints. `handlers`
 * supplies the response for each verb the view can call. Returns nothing;
 * register before navigating.
 */
async function stubPurchaseApi(
  page: Page,
  handlers: {
    get: () => unknown;
    answer?: (route: Route) => unknown;
    refine?: (route: Route) => unknown;
  },
) {
  await page.route(
    /\/api\/billing\/questions\/purchases\/\d+(\/(answer|refine))?$/,
    (route) => {
      const url = route.request().url();
      const method = route.request().method();
      if (method === "POST" && url.endsWith("/answer")) {
        return handlers.answer
          ? handlers.answer(route)
          : json(route, handlers.get());
      }
      if (method === "POST" && url.endsWith("/refine")) {
        return handlers.refine
          ? handlers.refine(route)
          : json(route, handlers.get());
      }
      return json(route, handlers.get());
    },
  );
}

test.describe("post-purchase answer view (ABS-321)", () => {
  test("a captured answer renders with the ABS-313 disclaimer", async ({
    page,
  }) => {
    await stubPurchaseApi(page, { get: () => purchase() });
    await page.goto("/app/answers/321");

    await expect(page.getByTestId("answer-body")).toContainText(
      /Based on the bylaw evidence/i,
    );
    await expect(page.getByTestId("answer-body")).toContainText(
      "RC-LUB §15.4(a)",
    );

    const disclaimer = page.getByTestId("paid-answer-disclaimer");
    await expect(disclaimer).toBeVisible();
    await expect(disclaimer).toContainText(
      /not an official municipal determination/i,
    );
    await expect(disclaimer).toContainText(/verify before relying/i);

    await expect(page.getByTestId("refine-remaining")).toContainText(
      /3 FOLLOW-UPS LEFT/i,
    );
  });

  test("an authorized purchase auto-runs the answer then renders it", async ({
    page,
  }) => {
    let answerCalled = false;
    await stubPurchaseApi(page, {
      // First load: card authorized but the engine hasn't run.
      get: () => purchase({ status: "authorized", answer: null }),
      answer: (route) => {
        answerCalled = true;
        return json(route, purchase({ status: "captured" }));
      },
    });
    await page.goto("/app/answers/321");

    await expect(page.getByTestId("answer-body")).toContainText(
      /Based on the bylaw evidence/i,
    );
    expect(answerCalled).toBe(true);
  });

  test("an in-window refinement updates the answer and decrements the counter", async ({
    page,
  }) => {
    await stubPurchaseApi(page, {
      get: () => purchase(),
      refine: (route) =>
        json(
          route,
          purchase({
            answer:
              "Refined: (1) four units permitted, (2) lot ≥ 400 m², " +
              "(3) Source: RC-LUB §15.4(a).",
            refinement_count: 1,
            refinements_remaining: 2,
          }),
        ),
    });
    await page.goto("/app/answers/321");
    await expect(page.getByTestId("answer-body")).toBeVisible();

    await page.getByTestId("refine-input").fill("Summarize in three bullets.");
    await page.getByTestId("refine-submit").click();

    await expect(page.getByTestId("answer-body")).toContainText(/Refined:/);
    await expect(page.getByTestId("refine-remaining")).toContainText(
      /2 FOLLOW-UPS LEFT/i,
    );
  });

  test("a materially new question is refused inline and routed to a new purchase", async ({
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
    await page.goto("/app/answers/321");
    await page
      .getByTestId("refine-input")
      .fill("What about 999 Oak Avenue instead?");
    await page.getByTestId("refine-submit").click();

    const notice = page.getByTestId("refine-notice");
    await expect(notice).toContainText(/different question/i);
    await expect(notice).toContainText(/question menu/i);
  });

  test("an exhausted refinement window dead-ends to buying a new answer", async ({
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
    await page.goto("/app/answers/321");
    await page.getByTestId("refine-input").fill("One more reformat please.");
    await page.getByTestId("refine-submit").click();

    await expect(page.getByTestId("refine-notice")).toContainText(
      /window closed/i,
    );
    // Composer is replaced by the closed-window dead-end.
    await expect(page.getByTestId("refine-closed")).toContainText(
      /buy a new answer/i,
    );
    await expect(page.getByTestId("refine-input")).toHaveCount(0);
  });

  test("a purchase with no follow-ups left hides the composer", async ({
    page,
  }) => {
    await stubPurchaseApi(page, {
      get: () =>
        purchase({ refinement_count: 3, refinements_remaining: 0 }),
    });
    await page.goto("/app/answers/321");

    await expect(page.getByTestId("answer-body")).toBeVisible();
    await expect(page.getByTestId("refine-input")).toHaveCount(0);
    await expect(page.getByTestId("refine-closed")).toContainText(
      /used all the follow-ups/i,
    );
  });

  test("a voided (ungroundable) question shows the no-charge explanation", async ({
    page,
  }) => {
    await stubPurchaseApi(page, {
      get: () =>
        purchase({
          status: "voided",
          answer: null,
          failure_reason: "zero_evidence",
        }),
    });
    await page.goto("/app/answers/321");

    const failed = page.getByTestId("answer-failed");
    await expect(failed).toContainText(/couldn.t be answered/i);
    await expect(failed).toContainText(/not charged/i);

    // ABS-364: recovery from a failed report must return to the in-app
    // question menu (/cases/new), not the marketing /pricing page — the
    // marketing page dead-ends the user's post-purchase recovery path.
    const backLink = failed.getByRole("link", {
      name: /back to the question menu/i,
    });
    await expect(backLink).toHaveAttribute("href", "/cases/new");
  });
});
