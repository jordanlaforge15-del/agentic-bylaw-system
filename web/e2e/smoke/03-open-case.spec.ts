// Smoke: /cases/new is the FREE, conversation-first entry point (ABS-385).
//
// The beta pivot inverts the old "buy an answer" surface: opening a case is
// free and starts a conversation. The user anchors to a property, asks their
// question, and clicks "Start the conversation — free →". A case is created
// via POST /api/cases (no purchase, no credit) and the browser lands on
// /app?case_id=N with the question auto-sent (?first_message=), which streams
// a mock assistant reply.
//
// This smoke pins the critical path:
//   * the designed page renders (kicker + free CTA)
//   * the free CTA opens a case and routes into the chat product
//   * the first message auto-sends and the SSE stream renders a reply

import { expect, test } from "../fixtures/test-env";

test("the free CTA opens a case and auto-sends the first message into chat", async ({
  page,
}) => {
  await page.goto("/cases/new");

  // The designed conversation-first surface.
  await expect(page.getByText("ACCOUNT · NEW CONVERSATION").first()).toBeVisible();
  await expect(
    page.getByRole("heading", { name: /Start a conversation/ }),
  ).toBeVisible();

  // Anchor + question, then start the (free) conversation.
  const anchor = `123 Smoke St ${Date.now()}`;
  await page.getByPlaceholder(/1234 Main St, Halifax/).fill(anchor);
  await page
    .getByPlaceholder(/Ask your question/)
    .fill("What is the minimum front yard setback?");

  await page.getByTestId("start-conversation-btn").click();

  // We land in the chat product with a fresh case bound.
  await page.waitForURL(/\/app\?case_id=\d+/);

  // The product shell connects and the auto-sent first message streams a
  // mock assistant reply (proves ?first_message= auto-send end-to-end).
  await expect(page.getByText(/Connected · Regional Centre LUB/)).toBeVisible();
  await expect(page.getByTestId("chat-thread")).toContainText(
    /Based on the bylaw evidence/i,
    { timeout: 15_000 },
  );
});
