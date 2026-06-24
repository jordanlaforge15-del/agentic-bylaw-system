// Functional: ABS-333 — a Conversation chat turn carrying a NUL byte must
// not crash persistence (the same class of bug ABS-332 fixed for Answers).
//
// Root cause: real bylaw evidence is extracted from municipal PDFs that
// carry stray NUL (0x00) bytes. A NUL rides untouched through the engine
// into the persisted chat history — advisor_chat_message.content_json
// (Postgres `jsonb`) — written by the on_turn_complete hook's db.flush()
// inside send_user_message_blocking, BEFORE the SSE stream emits a single
// content event. Postgres rejects the NUL (unsupported \u0000 escape, "
// cannot be converted to text"); the DataError aborts the turn, so the
// assistant answer never streams (the UI shows the chat-error state
// instead of the reply). SQLite + the mock gateway accept the byte, so
// this only reproduces against the real Postgres-backed stack — exactly
// what the e2e suite runs.
//
// The MOCK_NUL_BYTES sentinel makes the mock gateway ground with a NUL in
// the tool_use input (round one → assistant tool_use, persisted to JSONB)
// and answer with a NUL in the final text (round two → assistant text,
// persisted to JSONB), reproducing the rejected writes through the real
// Next-proxy ↔ FastAPI ↔ Postgres chain.

import {
  DEMO_USER_ID,
  E2E_API_URL,
  expect,
  openCaseViaApi,
  test,
} from "../fixtures/test-env";

const NUL = "\u0000";

test("a NUL byte in a grounded chat turn is scrubbed, not 500'd (real Postgres)", async ({
  page,
  request,
}) => {
  // Unique case → the chat session this turn creates is uniquely
  // identifiable by case_id in the session list (no nonce matching).
  const { caseId } = await openCaseViaApi();
  await page.goto(`/app?case_id=${caseId}`);

  const textarea = page.getByPlaceholder(/Ask about this parcel/);
  await expect(textarea).toBeVisible();
  await textarea.scrollIntoViewIfNeeded();

  // Gate input on React hydration (see smoke/04-chat-sse.spec.ts for the
  // rationale): a controlled <textarea> filled before React attaches gets
  // overwritten back to empty on hydration, leaving Send disabled.
  await page.waitForFunction(() => {
    const el = document.querySelector(
      'textarea[placeholder^="Ask about this parcel"]',
    );
    if (!el) return false;
    return Object.keys(el).some((k) => k.startsWith("__reactProps"));
  });

  await textarea.fill("Can I build a duplex here? MOCK_NUL_BYTES");
  await textarea.press("Enter");

  // The assistant reply renders → the on_turn_complete persistence ran
  // without a DataError. Without the scrub, the turn aborts at db.flush()
  // before this text ever streams, and this assertion times out.
  await expect(page.getByTestId("chat-thread")).toContainText(
    /permitted use is confirmed/i,
    { timeout: 15_000 },
  );

  // Belt-and-braces: read the persisted history straight off FastAPI and
  // assert the JSONB it wrote carries no NUL — i.e. the rows are writable
  // to a real Postgres jsonb column. GET is read-only (no terms gate), so
  // X-Test-User-Id auth is enough.
  const listRes = await request.get(`${E2E_API_URL}/v1/chat/sessions`, {
    headers: { "X-Test-User-Id": DEMO_USER_ID },
  });
  expect(listRes.status(), await listRes.text()).toBe(200);
  const { sessions } = (await listRes.json()) as {
    sessions: { session_id: string; case_id: number | null }[];
  };
  const mine = sessions.find((s) => s.case_id === caseId);
  expect(mine, `no session attached to case ${caseId}`).toBeTruthy();

  const sessionRes = await request.get(
    `${E2E_API_URL}/v1/chat/sessions/${mine!.session_id}`,
    { headers: { "X-Test-User-Id": DEMO_USER_ID } },
  );
  expect(sessionRes.status(), await sessionRes.text()).toBe(200);
  const body = await sessionRes.json();
  const serialized = JSON.stringify(body.messages);
  // Both the raw byte and its JSON escape are illegal in Postgres jsonb.
  expect(serialized).not.toContain(NUL);
  expect(serialized).not.toContain("\\u0000");
  // The turn actually persisted a tool round-trip + answer, not an empty
  // transcript — guards against "passes because nothing was written".
  expect(body.messages.length).toBeGreaterThanOrEqual(3);
});
