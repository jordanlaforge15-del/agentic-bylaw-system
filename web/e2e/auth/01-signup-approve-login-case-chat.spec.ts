// Flow 1 — trial sign-up happy path.
//
// What we simulate:
//   * Anonymous visitor submits the /signup invite-request form
//     (real Next.js route, real DB row in invite_request).
//   * Admin "approves" the invite — in production this goes through
//     /api/admin/invites/{id}/approve which calls Clerk's allowlist
//     API; here we skip Clerk and write the approved row directly
//     via the test-only /v1/_test/invite-approve endpoint.
//   * The user lands a sign-in session with a fresh identity. On
//     first request the e2e user-dependency JIT-creates the
//     advisor_user row, matches the approved invite by email, and
//     grants the free trial token wallet (ADVISOR_SIGNUP_TOKEN_GRANT,
//     ~25k tokens). That wallet — NOT any tier credit — is what funds
//     the first chat turn under the beta turn-based model.
//   * The user opens a case (FREE post-pivot: no tier, no credit
//     consumed) and gets a streamed SSE answer.
//
// Why this is the load-bearing spec of the suite: it asserts that
// the *post-Clerk* code path (resolve_or_create_user mirror + invite
// redemption + signup token grant) is reachable end-to-end and that a
// brand-new user with ZERO tier credits can open a free case and
// finish the first chat turn on their trial token balance. Flows 2
// and 3 build on the identity this one mints.

import {
  acceptCurrentTermsAs,
  approveInviteForEmail,
  expect,
  mintTestIdentity,
  openCaseAsIdentity,
  signInAs,
  submitInviteRequest,
  test,
} from "./fixtures";

test("sign-up → approve → login → case → chat", async ({
  context,
  page,
}) => {
  const identity = mintTestIdentity("signup");

  // 1. Anonymous request-invite via /api/invite. Driving the UI here
  //    so the marketing form is exercised at least once in CI.
  await submitInviteRequest(context, {
    email: identity.email,
    name: identity.fullName,
    viaUi: true,
    page,
  });

  // 2. Admin approval — grant ZERO tier credits. Post-pivot a case is
  //    free to open and chat bills the trial token wallet, so no tier
  //    credit is needed anywhere in this flow. Passing 0 makes the
  //    "free open + wallet-funded chat" contract explicit.
  await approveInviteForEmail(context, {
    email: identity.email,
    name: identity.fullName,
    starter_credits: 0,
  });

  // 3. First sign-in for this identity. signInAs mints the password
  //    gate cookie and the X-Test-User-Id / -Email / -Full-Name
  //    cookies the proxy forwards. The very next backend request
  //    (the case-open POST below, via /api/cases) hits the e2e
  //    user-dependency, which JIT-inserts the advisor_user row and
  //    redeems the approved invite.
  await signInAs(context, identity);

  // Clear the T&C click-wrap gate (ABS-18) so /app renders the chat
  // shell instead of redirecting to /app/terms. The UI flow is
  // covered separately by terms-acceptance-gate.spec.ts.
  await acceptCurrentTermsAs(context, identity);

  // 4. Visit the migrated case-open surface so the spec still covers the
  //    entry-flow UI seam (ABS-320: it now renders the priced-question
  //    menu, not the retired tier selector). The first authenticated
  //    backend request here JIT-inserts the advisor_user row and redeems
  //    the approved invite.
  await page.goto("/cases/new");
  await expect(page.getByTestId("question-menu")).toBeVisible();

  // A case is a free container; opening one is free (no tier, no credit)
  // and exercises the post-Clerk identity path. We open it via the
  // authenticated API (the form sells answers rather than minting the
  // container) and jump into the chat product with the case bound.
  const anchor = `Signup Flow ${identity.subUserId}`;
  const { caseId } = await openCaseAsIdentity(context, identity, {
    anchorLabel: anchor,
  });
  await page.goto(`/app?case_id=${caseId}`);
  await page.waitForURL(/\/app\?case_id=\d+/);

  // 5. Ask a question and verify the SSE stream renders the
  //    deterministic mock answer. The dispatcher's _DEFAULT_CITATION
  //    is RC-LUB §15.4; matching that proves the full chat pipeline
  //    is working under the newly minted identity.
  const textarea = page.getByPlaceholder(/Ask about this parcel/);
  await expect(textarea).toBeVisible();
  await textarea.scrollIntoViewIfNeeded();
  await textarea.fill("What is the minimum front yard setback?");
  await textarea.press("Enter");
  await expect(
    page.getByTestId("chat-thread"),
  ).toContainText(/Based on the bylaw evidence/i, { timeout: 15_000 });
});
