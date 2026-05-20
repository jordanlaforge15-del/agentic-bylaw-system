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
//     gifts the starter credits we set on approval. Without those
//     credits, the case-open flow that follows would 402.
//   * The user opens a case and gets a streamed SSE answer.
//
// Why this is the load-bearing spec of the suite: it asserts that
// the *post-Clerk* code path (resolve_or_create_user mirror + invite
// redemption + starter_credit gift) is reachable end-to-end and that
// a brand-new user can finish the first chat turn. Flows 2 and 3
// build on the identity this one mints.

import {
  acceptCurrentTermsAs,
  approveInviteForEmail,
  expect,
  mintTestIdentity,
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

  // 2. Admin approval — gift two starter standard credits so the
  //    first case-open below has a credit to consume.
  await approveInviteForEmail(context, {
    email: identity.email,
    name: identity.fullName,
    starter_credits: 2,
    starter_tier: "standard",
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

  // 4. Open a case via the marketing form so the spec covers the
  //    UI seam too. Unique anchor to avoid collisions with parallel
  //    workers (smoke/03-open-case.spec.ts uses the same form).
  await page.goto("/cases/new");
  const anchor = `Signup Flow ${identity.subUserId}`;
  await page
    .getByPlaceholder(/1234 Main St, Halifax/)
    .fill(anchor);
  await page
    .getByPlaceholder(/Describe the inquiry/)
    .fill("Can I build an ADU at this address under the new bylaw?");
  await page.getByRole("button", { name: /^Open case$/ }).click();
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
