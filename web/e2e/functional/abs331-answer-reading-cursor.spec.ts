// Functional: ABS-331 → superseded in-flight UX by ABS-343.
//
// ABS-331 originally reinstated the animated "reading the bylaw" cursor
// while the engine was grounding the answer. ABS-343 replaces that in-flight
// affordance on this surface with the dedicated generation view (the
// report's own letterhead + a six-step progress bar), so this spec now
// asserts THAT view appears while the answer run is in flight, then hands
// off to the grounded answer. It keeps the original structure — drive the
// real answer view, stub the proxy at the network boundary (like abs321),
// hold the POST /answer open so the in-flight view is observable, then
// release it and assert the grounded answer renders.

import { expect, test } from "../fixtures/test-env";
import type { Route } from "@playwright/test";

const GROUNDED_ANSWER =
  "Based on the bylaw evidence, a four-unit dwelling is permitted. " +
  "Source: RC-LUB §15.4(a).";

function basePurchase(overrides: Record<string, unknown> = {}) {
  return {
    id: 331,
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

test("an authorized purchase plays the generation view while answering, then hands off to the answer", async ({
  page,
}) => {
  // Gate the POST /answer resolution behind a promise the test controls, so
  // the in-flight generation view stays on screen long enough to assert
  // before the grounded answer replaces it.
  let releaseAnswer: () => void = () => {};
  const answerGate = new Promise<void>((resolve) => {
    releaseAnswer = resolve;
  });

  await page.route(
    /\/api\/billing\/questions\/purchases\/\d+(\/answer)?$/,
    async (route) => {
      const url = route.request().url();
      const method = route.request().method();
      if (method === "POST" && url.endsWith("/answer")) {
        await answerGate;
        return json(route, basePurchase({ status: "captured" }));
      }
      // Initial GET: card authorized, engine has not run yet.
      return json(route, basePurchase({ status: "authorized", answer: null }));
    },
  );

  await page.goto("/app/answers/331");

  // ABS-343: the in-flight affordance is now the six-step generation view
  // (letterhead + progress bar), not the "ABS · READING" cursor.
  await expect(page.getByTestId("report-generating")).toBeVisible({
    timeout: 8_000,
  });
  await expect(page.getByTestId("gen-step")).toHaveCount(6);
  await expect(page.getByText("ABS · READING")).toHaveCount(0);

  // Release the engine — the grounded answer replaces the generation view.
  releaseAnswer();
  await expect(page.getByTestId("answer-body")).toContainText(
    /Based on the bylaw evidence/i,
  );
  await expect(page.getByTestId("report-generating")).toHaveCount(0);
});
