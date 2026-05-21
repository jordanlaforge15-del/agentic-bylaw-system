// Flow 2 — session resume after logout on the same case.
//
// The closest existing coverage is functional/multi-turn.spec.ts which
// runs two consecutive turns in a single session, no logout between
// them. That spec was added in response to a session-resume bug where
// the second turn 404'd because the in-memory ChatSession's user_id
// disagreed with the DB-resolved User.id. The same bug surface
// reopens once we put a logout between turns: the second login mints
// a brand-new browser context state, every per-context cache drops,
// and the resume path has to find the case + the user's sessions by
// the same stable identity. This spec asserts that holds end-to-end:
//
//   * Approve → first sign-in → open a case → first turn streams.
//   * Sign out (clear all auth cookies). Confirm /app now redirects
//     through /access.
//   * Sign in again as the same identity, navigate to the case URL,
//     send a follow-up question. The follow-up runs as a fresh chat
//     session under the same case (the /app page doesn't auto-resume
//     prior history from URL alone — users do that via /cases) but
//     must successfully bill to the case-credit AND show up in
//     /v1/chat/sessions alongside the first turn's session.
//   * Two distinct sessions on the same case + same user is the
//     load-bearing assertion: a user_id-mismatch regression makes
//     the second send 401/404 OR makes the second session attach
//     to a phantom user row, which surfaces as < 2 sessions when we
//     re-list /v1/chat/sessions as the original identity.

import {
  acceptCurrentTermsAs,
  approveInviteForEmail,
  E2E_API_URL,
  expect,
  mintTestIdentity,
  openCaseAsIdentity,
  signInAs,
  signOut,
  submitInviteRequest,
  test,
} from "./fixtures";

test("logout / login keeps the same case usable for a new turn", async ({
  context,
  page,
}) => {
  const identity = mintTestIdentity("resume-same");

  await submitInviteRequest(context, {
    email: identity.email,
    name: identity.fullName,
  });
  await approveInviteForEmail(context, {
    email: identity.email,
    name: identity.fullName,
    starter_credits: 2,
    starter_tier: "standard",
  });

  // First login.
  await signInAs(context, identity);

  // Clear the T&C click-wrap gate (ABS-18) for this identity. One
  // acceptance row carries across the sign-out / sign-in cycle below
  // because both sessions resolve to the same advisor_user row.
  await acceptCurrentTermsAs(context, identity);

  // Open a case via API to bypass the form. The post hits the e2e
  // backend with this identity's headers, triggering JIT-create +
  // invite redemption inside the user dependency.
  const { caseId } = await openCaseAsIdentity(context, identity, {
    anchorLabel: `Resume Same ${identity.subUserId}`,
  });

  await page.goto(`/app?case_id=${caseId}`);
  const textarea = page.getByPlaceholder(/Ask about this parcel/);
  const thread = page.getByTestId("chat-thread");

  // First turn — establishes a chat session bound to {caseId, user}.
  await expect(textarea).toBeVisible();
  await textarea.fill("First question before signing out.");
  await textarea.press("Enter");
  await expect(thread).toContainText(/Based on the bylaw evidence/i, {
    timeout: 15_000,
  });

  // Sign out — proxy gate + identity cookies both drop. A bare
  // navigation to /app should now redirect through the access gate,
  // matching Clerk's sign-out under the password-gate fallback.
  await signOut(context);
  await page.goto("/app");
  await page.waitForURL(/\/access(\?|$)/);

  // Sign back in as the SAME identity. The e2e backend resolves to
  // the existing advisor_user row (no second insert), so the case
  // opened in the first session remains attached.
  await signInAs(context, identity);

  // Reload /app at the case URL and send a NEW question — this
  // starts a fresh chat session under the existing case. If a
  // user_id mismatch sneaks in, the case-credit reservation or
  // session insert below 401/404s and we never see a streamed
  // reply.
  await page.goto(`/app?case_id=${caseId}`);
  await expect(textarea).toBeVisible({ timeout: 10_000 });
  await textarea.fill("Follow-up question after logout/login.");
  await textarea.press("Enter");
  await expect(thread).toContainText(/Based on the bylaw evidence/i, {
    timeout: 15_000,
  });

  // Direct check: /v1/chat/sessions for this identity should now
  // list both sessions (first turn + post-relogin turn) under the
  // same user. The summary shape doesn't expose case_id, but the
  // count alone is enough: every session belongs to this identity
  // (each spec mints a unique sub-user-id), so two sessions means
  // both writes attached to the same advisor_user row. A user_id
  // mismatch would either 4xx the second send (no streamed reply,
  // already asserted above) or attach the second session to a
  // phantom row, shrinking this list to one.
  const res = await context.request.get(`${E2E_API_URL}/v1/chat/sessions`, {
    headers: {
      "X-Test-User-Id": identity.subUserId,
      "X-Test-User-Email": identity.email,
    },
  });
  expect(res.ok()).toBeTruthy();
  const data = (await res.json()) as { sessions: unknown[] };
  expect(data.sessions.length).toBeGreaterThanOrEqual(2);
});
