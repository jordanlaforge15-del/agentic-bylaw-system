// Functional: the case-open flow never drops the user's first message.
//
// Regression context (ABS-449): /cases/new redirects to
// /app?case_id=N&first_message=... and the auto-send effect strips the
// param with router.replace BEFORE awaiting send(). That re-runs the
// session-restore effect one render later with the param gone — and by
// then POST /v1/chat has already created the (still empty) chat session
// row. The restore effect found that row, called selectSession(), and
// selectSession aborted the very stream that created it:
//
//   POST /api/chat → 200 OK [FAILED: net::ERR_ABORTED]
//
// The question was never persisted, no error was shown, and the case sat
// with an unanswered question forever — on the app's primary entry flow.
//
// Three paths covered here:
//   1. Fresh case (the literal repro): the auto-sent question must be
//      persisted server-side, not just painted optimistically.
//   2. Case that already has a session: the deterministic form of the same
//      race — the restore effect finds the OLD session instantly and, before
//      the fix, replaced the transcript with it, erasing the new question.
//   3. A first-message send that genuinely fails must surface a visible
//      error plus a Retry affordance rather than failing silently.

import type { Page } from "@playwright/test";

import {
  DEMO_USER_ID,
  E2E_API_URL,
  expect,
  openCaseViaApi,
  test,
} from "../fixtures/test-env";

const AUTH = { "X-Test-User-Id": DEMO_USER_ID };

/**
 * Keep POST /api/chat in flight for `ms` before it is dispatched, so the turn
 * is still unresolved when the router.replace that strips `first_message`
 * round-trips and re-runs the session-restore effect.
 *
 * That window is the whole bug. Against a live model a turn runs for tens of
 * seconds; the mock gateway answers in well under one, so without this the
 * turn is finished before the restore effect ever re-fires and the race can't
 * be observed at all. Delaying the dispatch (rather than the response) is what
 * makes an abort during the window destructive in the same way it is in
 * production: the backend never sees the question.
 *
 * Only matches `/api/chat` itself — `/api/chat/sessions*` is untouched, which
 * matters: that GET is the request that used to trigger the abort.
 */
async function slowChatTurn(page: Page, ms: number): Promise<void> {
  await page.route("**/api/chat", async (route) => {
    await new Promise((resolve) => setTimeout(resolve, ms));
    try {
      await route.continue();
    } catch {
      // The page aborted this request (that IS the regression) or navigated
      // away. The assertions below report it, not this handler.
    }
  });
}

/** All user-message text persisted under the given case, straight from the
 * backend — bypasses the UI entirely so an optimistic bubble can't fake it. */
async function persistedUserMessages(caseId: number): Promise<string[]> {
  const listRes = await fetch(`${E2E_API_URL}/v1/chat/sessions`, {
    headers: AUTH,
  });
  expect(listRes.ok).toBeTruthy();
  const { sessions } = (await listRes.json()) as {
    sessions: Array<{ session_id: string; case_id?: number | null }>;
  };
  const out: string[] = [];
  for (const s of sessions.filter((s) => s.case_id === caseId)) {
    const res = await fetch(
      `${E2E_API_URL}/v1/chat/sessions/${encodeURIComponent(s.session_id)}`,
      { headers: AUTH },
    );
    if (!res.ok) continue;
    const data = (await res.json()) as {
      messages: Array<{ role: string; content: unknown }>;
    };
    for (const m of data.messages) {
      if (m.role === "user" && typeof m.content === "string") {
        out.push(m.content);
      }
    }
  }
  return out;
}

test("the auto-sent first message is persisted, not aborted", async ({
  page,
}) => {
  const { caseId } = await openCaseViaApi({
    anchorLabel: `9401 First Msg ${Date.now()}, Halifax`,
  });
  const question = "What is the maximum building height on this lot?";

  await slowChatTurn(page, 3_000);
  await page.goto(
    `/app?case_id=${caseId}&first_message=${encodeURIComponent(question)}`,
  );

  const thread = page.getByTestId("chat-thread");
  await expect(thread).toContainText(/Based on the bylaw evidence/i, {
    timeout: 15_000,
  });
  // The question stays in the transcript alongside the answer.
  await expect(thread).toContainText(question);

  // The param is still stripped once the turn is under way (a refresh must
  // not replay the send) — the fix keeps that behaviour.
  await expect(page).not.toHaveURL(/first_message=/);

  // The load-bearing assertion: the backend actually has the question. The
  // aborted turn used to leave a session with zero messages.
  expect(await persistedUserMessages(caseId)).toContain(question);
});

test("first message on a case that already has a session isn't swallowed", async ({
  page,
}) => {
  const { caseId } = await openCaseViaApi({
    anchorLabel: `9402 Second Turn ${Date.now()}, Halifax`,
  });
  const first = "What is the minimum front yard setback?";
  const second = "What is the maximum lot coverage for this parcel?";

  // Seed the case with a completed session.
  await page.goto(
    `/app?case_id=${caseId}&first_message=${encodeURIComponent(first)}`,
  );
  const thread = page.getByTestId("chat-thread");
  await expect(thread).toContainText(/Based on the bylaw evidence/i, {
    timeout: 15_000,
  });

  // Re-enter the same case through the case-open flow with a NEW question.
  // Pre-fix, the restore effect found the seeded session mid-turn, aborted
  // the in-flight stream, and replaced the transcript with the old history —
  // the new question vanished from the thread and from the database.
  await slowChatTurn(page, 3_000);
  await page.goto(
    `/app?case_id=${caseId}&first_message=${encodeURIComponent(second)}`,
  );
  await expect(thread).toContainText(second, { timeout: 15_000 });
  await expect(thread).toContainText(/Based on the bylaw evidence/i, {
    timeout: 15_000,
  });

  const persisted = await persistedUserMessages(caseId);
  expect(persisted).toContain(second);
});

test("a failed first message shows an error and a working retry", async ({
  page,
}) => {
  const { caseId } = await openCaseViaApi({
    anchorLabel: `9403 Retry ${Date.now()}, Halifax`,
  });
  const question = "What is the maximum floor area ratio here?";

  // Fail only the first POST /api/chat; the retry goes through untouched.
  let failed = false;
  await page.route("**/api/chat", async (route) => {
    if (!failed) {
      failed = true;
      await route.abort("failed");
      return;
    }
    await route.continue();
  });

  await page.goto(
    `/app?case_id=${caseId}&first_message=${encodeURIComponent(question)}`,
  );

  // The dropped question must be visible as an error, not silence.
  const error = page.getByTestId("chat-error");
  await expect(error).toBeVisible({ timeout: 15_000 });
  const retry = page.getByTestId("chat-retry");
  await expect(retry).toBeVisible();
  // The question itself is still in the transcript to retry.
  const thread = page.getByTestId("chat-thread");
  await expect(thread).toContainText(question);

  await retry.click();

  await expect(thread).toContainText(/Based on the bylaw evidence/i, {
    timeout: 15_000,
  });
  await expect(error).toBeHidden();
  // Retrying must not double-post the question — one bubble, one row.
  await expect(
    thread.getByTestId("user-message").filter({ hasText: question }),
  ).toHaveCount(1);
  expect(await persistedUserMessages(caseId)).toEqual([question]);
});
