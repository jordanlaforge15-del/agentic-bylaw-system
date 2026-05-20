// Functional: the sidebar surfaces the case anchor + first question
// for newly-created cases, not the "New reading" placeholder.
//
// Regression context (ABS-22): the listing endpoint used to return
// lightweight ChatSession objects with empty `messages`, so the
// summary title always fell through to "New reading" — and the title
// never carried the case anchor (the address the case was opened
// against), so users couldn't tell their cases apart in the sidebar.
// This spec drives the full case-open + first-message + sidebar
// refresh loop and asserts both pieces show up.
//
// Functional specs run on the desktop-chrome project only (per
// playwright.config.ts), so the desktop sidebar is always rendered
// in-flow — no drawer toggle dance needed.

import { expect, openCaseViaApi, test } from "../fixtures/test-env";

test("sidebar title shows case anchor and question after first turn", async ({
  page,
}) => {
  // A unique anchor per run so reruns don't collide on the 30-day
  // case-match window. Includes a recognisable prefix so the
  // assertion below is unambiguous against any other rows in the
  // seed user's history.
  const anchorLabel = `9001 Sidebar St ${Date.now()}, Halifax`;
  const { caseId } = await openCaseViaApi({ anchorLabel });

  // Navigate with `first_message` so /app auto-sends the opening
  // question — this is the exact URL the case-open form produces, so
  // the test mirrors the real entry path.
  const firstMessage = "What is the minimum front yard setback?";
  await page.goto(
    `/app?case_id=${caseId}&first_message=${encodeURIComponent(firstMessage)}`,
  );

  // Wait until the assistant streams the deterministic mock answer.
  // Only then has the chat session been persisted with the first user
  // message, and only then does the sidebar refetch (driven by the
  // page's post-stream `setSidebarRefresh` bump) include this row.
  await expect(page.getByTestId("chat-thread")).toContainText(
    /Based on the bylaw evidence/i,
    { timeout: 15_000 },
  );

  // The desktop sidebar is the first <aside> on the page (the parcel
  // pane is also an <aside>, but it lives later in the flex row).
  const sidebar = page.locator("aside").first();

  // The newly-minted row's title contains BOTH the anchor address
  // and (the start of) the first question. The middle-dot separator
  // is incidental — asserting on each substring keeps the test
  // resilient if the separator changes later.
  const row = sidebar.getByRole("button", {
    name: new RegExp(escapeRegExp(anchorLabel), "i"),
  });
  await expect(row).toBeVisible({ timeout: 5_000 });
  await expect(row).toContainText(/minimum front yard setback/i);

  // And: the row is NOT the "New reading" placeholder. The placeholder
  // would only appear if `list_summaries_for_user` failed to load the
  // case + first message, which is the exact regression this spec
  // guards against.
  await expect(row).not.toHaveText(/^\s*New reading\s*$/);
});

function escapeRegExp(s: string): string {
  return s.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
}
