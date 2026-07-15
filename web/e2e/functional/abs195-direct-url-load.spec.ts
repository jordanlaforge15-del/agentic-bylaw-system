// Functional: direct URL load of /app?case_id=N restores transcript.
//
// Regression context (ABS-195): navigating to /app?case_id=N via
// reload, share link, or browser back/forward rendered an empty
// transcript and "No parcel yet" even though the case had existing
// sessions. The fix adds a session-restore effect that fires on mount
// when case_id is in the URL but no first_message is present.
//
// This spec tests three paths:
//   1. Reload: open a case, send a message, reload the page — transcript
//      should survive.
//   2. Direct share link: navigate cold to /app?case_id=N for an
//      existing case with history — transcript and the balance strip render.
//   3. Footer balance strip: the "Case #N · ~N turns left" strip in the
//      footer should appear after restore (regression from the same
//      issue). ABS-386 replaced the old tier/budget strip with this
//      turns-based BalanceStrip.

import { expect, openCaseViaApi, test } from "../fixtures/test-env";

test("reloading /app?case_id=N restores transcript", async ({
  page,
}) => {
  const { caseId } = await openCaseViaApi();

  // Navigate with first_message to send the opening turn.
  const firstMessage = "What is the minimum front yard setback?";
  await page.goto(
    `/app?case_id=${caseId}&first_message=${encodeURIComponent(firstMessage)}`,
  );

  const thread = page.getByTestId("chat-thread");
  await expect(thread).toContainText(/Based on the bylaw evidence/i, {
    timeout: 15_000,
  });

  // Verify the transcript is populated before reload.
  await expect(thread).toContainText(/minimum front yard setback/i);

  // Reload — this is the primary repro path for ABS-195.
  await page.reload();

  // After reload, the restore effect should fetch sessions and
  // load the most recent one for this case_id. The user message
  // and assistant reply should both reappear.
  await expect(thread).toContainText(/minimum front yard setback/i, {
    timeout: 10_000,
  });
  await expect(thread).toContainText(/Based on the bylaw evidence/i, {
    timeout: 10_000,
  });
});

test("footer balance strip shows Case # and turns after direct URL restore", async ({
  page,
}) => {
  const { caseId } = await openCaseViaApi();

  // Send one turn first so the session exists.
  await page.goto(
    `/app?case_id=${caseId}&first_message=${encodeURIComponent("Test question")}`,
  );
  await expect(page.getByTestId("chat-thread")).toContainText(
    /Based on the bylaw evidence/i,
    { timeout: 15_000 },
  );

  // Now navigate cold (simulates a share link / direct URL load).
  await page.goto(`/app?case_id=${caseId}`);

  // The BalanceStrip footer must show the case number and a turns figure,
  // seeded from /api/billing/wallet — no tier vocabulary (ABS-386).
  const footer = page.getByTestId("balance-strip");
  await expect(footer).toContainText(/Case #/i, { timeout: 10_000 });
  await expect(footer).toContainText(/~\d+ turns? left/i);
  // No retired tier vocabulary (the disclosure's "complex questions" is fine).
  await expect(footer).not.toContainText(
    /tier|Quick Lookup|Standard Case|Complex File|budget/i,
  );
});
