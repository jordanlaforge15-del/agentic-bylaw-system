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
  await page.waitForURL(/\/access(\?|$)/);

  // Sign in again as the same identity; advisor_user row reuse is
  // what makes the second case attach correctly.
  await signInAs(context, identity);

  // Second case via the marketing form so the spec hits the open-
  // case UI surface at least once. Unique anchor per identity so
  // the 30-day match window doesn't collapse it onto the first
  // case.
  await page.goto("/cases/new");
  const secondAnchor = `Resume New Second ${identity.subUserId}`;
  await page
    .getByPlaceholder(/1234 Main St, Halifax/)
    .fill(secondAnchor);
  await page
    .getByPlaceholder(/Describe the inquiry/)
    .fill("Different parcel, follow-up question after a logout cycle.");
  await page.getByRole("button", { name: /^Open case$/ }).click();
  await page.waitForURL(/\/app\?case_id=\d+/);

  // Both cases must appear on /cases for the *same* user. We
  // assert by anchor label, which is unique per spec run.
  await page.goto("/cases");
  await expect(
    page.getByRole("heading", { level: 1, name: /My cases/ }),
  ).toBeVisible();
  await expect(page.getByText(firstAnchor)).toBeVisible();
  await expect(page.getByText(secondAnchor)).toBeVisible();
});
