// Functional: ABS-331 — the post-purchase answer surface must reinstate
// the app-page "reading the bylaw" UX while the engine is grounding the
// answer, instead of the static "Generating your answer" line it had
// regressed to.
//
// What changed: /app/answers/[id]'s answering phase now renders the same
// animated cursor the /app chat thread uses — the "ABS · READING" badge
// plus animated-ellipsis dots (.abs-ellipsis-dot), shared via the
// ReadingIndicator component. This spec drives the real answer view and
// stubs the priced-question proxy at the network boundary (same approach
// as abs321), holding the POST /answer open so the answering cursor is
// observable, then releasing it and asserting the grounded answer renders.

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

test("an authorized purchase shows the animated 'reading the bylaw' cursor while answering", async ({
  page,
}) => {
  // Gate the POST /answer resolution behind a promise the test controls,
  // so the answering phase stays on screen long enough to assert the
  // animated reading cursor before the grounded answer replaces it.
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

  // The reinstated app-page reading UX: the "ABS · READING" badge and the
  // animated-ellipsis dots (NOT a static "Generating your answer" line).
  await expect(page.getByText("ABS · READING")).toBeVisible({ timeout: 8_000 });
  await expect(page.locator(".abs-ellipsis-dot").first()).toBeVisible();
  await expect(page.getByTestId("answer-status")).toContainText(
    /reading the bylaw/i,
  );

  // Release the engine — the grounded answer replaces the cursor.
  releaseAnswer();
  await expect(page.getByTestId("answer-body")).toContainText(
    /Based on the bylaw evidence/i,
  );
  // The reading cursor is gone once the answer is in.
  await expect(page.getByText("ABS · READING")).toHaveCount(0);
});
