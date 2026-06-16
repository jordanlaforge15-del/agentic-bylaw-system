// Flow 3 — session resume after logout, then create a second case.
//
// Closest existing coverage is smoke/06-cases-list.spec.ts, which
// asserts that opening one case via the API makes it appear on
// /cases. That spec runs against the seeded demo user (no
// sign-up/login lifecycle) and tests a single case. This spec
// guards a different regression: when a user logs out and back in,
// and opens a SECOND case after re-auth, the /cases listing should
// show BOTH cases under the same user. A user_id mismatch on resume
// would orphan the second case onto a phantom user row, leaving
// the visible /cases count at one — exactly the failure mode this
// spec catches.

import {
  approveInviteForEmail,
  expect,
  mintTestIdentity,
  openCaseAsIdentity,
  signInAs,
  signOut,
  submitInviteRequest,
  test,
} from "./fixtures";

test("logout / login + open a second case shows both on /cases", async ({
  context,
  page,
}) => {
  const identity = mintTestIdentity("resume-new");

  await submitInviteRequest(context, {
    email: identity.email,
    name: identity.fullName,
  });
  await approveInviteForEmail(context, {
    email: identity.email,
    name: identity.fullName,
    // Need at least two credits — one for each case we'll open.
    starter_credits: 3,
    starter_tier: "standard",
  });

  // First login + first case.
  await signInAs(context, identity);
  const firstAnchor = `Resume New First ${identity.subUserId}`;
  await openCaseAsIdentity(context, identity, {
    anchorLabel: firstAnchor,
  });

  // Sign out — clears all auth cookies.
  await signOut(context);
  await page.goto("/app");
  await page.waitForURL(/\/sign-in/);

  // Sign in again as the same identity; advisor_user row reuse is
  // what makes the second case attach correctly.
  await signInAs(context, identity);

  // Visit the migrated case-open surface so the spec still hits that UI
  // seam (ABS-320: it now renders the priced-question menu rather than the
  // tier selector + "Open case" button).
  await page.goto("/cases/new");
  await expect(page.getByTestId("question-menu")).toBeVisible();

  // Second case via the authenticated API (a case is a free container; the
  // form sells answers rather than minting it). Unique anchor per identity
  // so the 30-day match window doesn't collapse it onto the first case.
  const secondAnchor = `Resume New Second ${identity.subUserId}`;
  await openCaseAsIdentity(context, identity, {
    anchorLabel: secondAnchor,
  });

  // Both cases must appear on /cases for the *same* user. We
  // assert by anchor label, which is unique per spec run.
  await page.goto("/cases");
  await expect(
    page.getByRole("heading", { level: 1, name: /My cases/ }),
  ).toBeVisible();
  await expect(page.getByText(firstAnchor)).toBeVisible();
  await expect(page.getByText(secondAnchor)).toBeVisible();
});
