// Functional: a backend failure surfaces in the chat thread as an
// error banner, not a silent dead-state. We force a 502 by routing
// /api/chat to a non-existent upstream for this one test.

import { expect, openCaseViaApi, test } from "../fixtures/test-env";

test("backend 5xx renders an error banner in the chat thread", async ({
  page,
}) => {
  const { caseId } = await openCaseViaApi();
  await page.goto(`/app?case_id=${caseId}`);

  // Intercept /api/chat and synthesize a 500 from the proxy. Doing it
  // at the browser layer (not the upstream FastAPI) keeps the test
  // self-contained.
  await page.route("**/api/chat", (route) => {
    route.fulfill({
      status: 500,
      contentType: "application/json",
      body: JSON.stringify({ error: "synthetic" }),
    });
  });

  await page
    .getByPlaceholder(/Ask about this parcel/)
    .fill("forced failure test");
  await page.getByRole("button", { name: /^Send/ }).click();

  await expect(
    page.getByText(/Backend error \(500\)/i),
  ).toBeVisible({ timeout: 10_000 });
});
