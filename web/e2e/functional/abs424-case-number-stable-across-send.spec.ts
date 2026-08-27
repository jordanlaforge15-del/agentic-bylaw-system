// Functional: ABS-424 — the footer's "CASE #N" must name the same case
// before and after a message is sent.
//
// The footer badge is `user_case_number` (per-user, small, durable). On a
// direct `/app?case_id=N` load the workspace hydrated it by scanning
// GET /api/cases — a list capped at the newest 50 cases. A user deep enough
// into their history opening an older case fell off that list entirely, so
// the badge had no number until the first turn's SSE `session` event
// delivered one. The reference number a user is told to attach to a permit
// application therefore appeared mid-conversation, or (before ABS-453 dropped
// the fallback) changed from the internal DB id to the user-facing number.
//
// The fix adds GET /v1/cases/{id} — an uncapped, per-case lookup — and points
// the workspace at it. This spec pins the invariant end to end: the same
// number on load and after a send, and never the internal advisor_case.id.
//
// The 50-case list cap is simulated by stubbing GET /api/cases with an empty
// list. That is exactly what a heavy user's browser receives for an old case,
// and it fails against the old code path (no other load-time source exists)
// while passing on the new one.

import {
  DEMO_USER_ID,
  E2E_API_URL,
  expect,
  test,
  waitForHydration,
} from "../fixtures/test-env";

type OpenedCase = { id: number; user_case_number: number };

async function openCase(anchorLabel: string): Promise<OpenedCase> {
  const res = await fetch(`${E2E_API_URL}/v1/cases`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      "X-Test-User-Id": DEMO_USER_ID,
    },
    body: JSON.stringify({ anchor_label: anchorLabel, anchor_kind: "address" }),
  });
  if (!res.ok) {
    throw new Error(`openCase failed: ${res.status} ${await res.text()}`);
  }
  return ((await res.json()) as { case: OpenedCase }).case;
}

/** A case whose DB id differs from its user-facing number — the only shape in
 * which the two identifiers are distinguishable in the UI. advisor_case.id is
 * global while user_case_number is per-user, so they diverge almost at once;
 * the loop only makes that deterministic. */
async function openDivergentCase(suffix: string): Promise<OpenedCase> {
  let opened = await openCase(`abs424-${suffix}-0`);
  for (let i = 1; i < 5 && opened.id === opened.user_case_number; i += 1) {
    opened = await openCase(`abs424-${suffix}-${i}`);
  }
  expect(
    opened.id,
    "need a case whose DB id differs from its user-facing number",
  ).not.toBe(opened.user_case_number);
  return opened;
}

test("footer case number is identical before and after sending a message", async ({
  page,
}) => {
  const suffix = `${Date.now()}-${Math.random().toString(36).slice(2, 7)}`;
  const { id: caseId, user_case_number: caseNum } =
    await openDivergentCase(suffix);

  // Stand in for the newest-N cap: the case list comes back without this
  // case, exactly as it does for a user with more than 50 cases.
  await page.route(
    (url) => url.pathname === "/api/cases",
    (route) =>
      route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({ cases: [] }),
      }),
  );

  await page.goto(`/app?case_id=${caseId}`);

  const strip = page.getByTestId("balance-strip");
  const correctBadge = new RegExp(`Case #${caseNum}\\b`, "i");
  // `\b` keeps "#7" from matching inside "#17" and vice versa.
  const dbIdBadge = new RegExp(`Case #${caseId}\\b`, "i");

  await expect(strip).toBeVisible({ timeout: 10_000 });
  await expect(strip).toContainText(correctBadge, { timeout: 10_000 });
  await expect(strip).not.toContainText(dbIdBadge);

  const textarea = page.getByPlaceholder(/Ask about this parcel/);
  await expect(textarea).toBeVisible();
  await textarea.scrollIntoViewIfNeeded();
  await waitForHydration(page, 'textarea[placeholder^="Ask about this parcel"]');
  await textarea.fill("What is the minimum front yard setback?");
  await textarea.press("Enter");

  // Wait for the turn to actually settle — the SSE `session` event (the
  // second hydration path for the number) has landed by then.
  await expect(page.getByTestId("chat-thread")).toContainText(
    /Based on the bylaw evidence/i,
    { timeout: 30_000 },
  );

  // Same number, still not the DB id.
  await expect(strip).toContainText(correctBadge);
  await expect(strip).not.toContainText(dbIdBadge);
});
