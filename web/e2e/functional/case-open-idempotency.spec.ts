// Functional: opening the same anchor twice is idempotent — second
// open returns the existing case and credit, no second credit claimed.
//
// Regression for ABS-8 (Linear): chris.rafuse@gmail.com hit a prod 402
// because two of his three standard credits were stuck in `reserved`
// state — a duplicate POST /v1/cases for the same anchor (or a chat
// session-start before ABS-9 landed) used to claim a second credit
// against the same case, leaving the first orphaned. With the
// open_case idempotency guard in place, both API-level and UI-level
// retries must return the *same* case_id + credit_id.
//
// Two specs in one file:
//   1. API-level — two direct POST /v1/cases calls.
//   2. UI-level  — open via the form, then re-submit the form with
//      the same anchor, assert the redirect lands on the same case_id.

import {
  DEMO_USER_ID,
  E2E_API_URL,
  expect,
  openCaseViaApi,
  test,
} from "../fixtures/test-env";

type OpenCaseResponseShape = {
  case: { id: number };
  credit_id: number;
  reused_existing_case: boolean;
};

async function openCaseRaw(anchorLabel: string): Promise<OpenCaseResponseShape> {
  const res = await fetch(`${E2E_API_URL}/v1/cases`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      "X-Test-User-Id": DEMO_USER_ID,
    },
    body: JSON.stringify({
      anchor_label: anchorLabel,
      anchor_kind: "address",
      tier: "standard",
    }),
  });
  if (!res.ok) {
    throw new Error(
      `openCaseRaw(${anchorLabel}) failed: ${res.status} ${await res.text()}`,
    );
  }
  return (await res.json()) as OpenCaseResponseShape;
}

test("duplicate POST /v1/cases for the same anchor returns the same case and credit", async () => {
  // Unique anchor so we don't collide with other parallel workers'
  // 30-day-window matches in the seeded DB.
  const anchor = `idem-api-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`;

  const first = await openCaseRaw(anchor);
  const second = await openCaseRaw(anchor);

  // Same case row both times — that's the case-match path, working as
  // designed in both pre- and post-fix code.
  expect(second.case.id).toBe(first.case.id);
  // Same credit row both times — this is the ABS-8 assertion. Before
  // the fix, the second call claimed a fresh available credit against
  // the same case; now it reuses the one already reserved.
  expect(second.credit_id).toBe(first.credit_id);
  // The flag is informational, but the second call must report the
  // existing-case match — otherwise the frontend would render a
  // "fresh case opened" toast for a reopen.
  expect(second.reused_existing_case).toBe(true);
});

test("clicking Open case for an already-open anchor reuses the same case", async ({
  page,
}) => {
  // Open the case via the API first so we have a known case_id to
  // compare against after the UI flow.
  const anchor = `idem-ui-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`;
  const { caseId: firstId } = await openCaseViaApi({ anchorLabel: anchor });

  await page.goto("/cases/new");
  const anchorInput = page.getByPlaceholder(/1234 Main St, Halifax/);
  await anchorInput.fill(anchor);
  await page
    .getByPlaceholder(/Describe the inquiry/)
    .fill("Same anchor re-opened — should not double-charge.");

  // Triggering blur surfaces the "EXISTING CASE FOUND" banner. The
  // user can click "Continue case" to navigate without a second POST,
  // but the bug we are guarding against is the user clicking the
  // form's main "Open case" button anyway (double-click, refresh,
  // habit). Drive that path explicitly.
  await anchorInput.blur();
  await expect(page.getByText(/EXISTING CASE FOUND/)).toBeVisible();

  await page.getByRole("button", { name: /^Open case$/ }).click();

  // Redirect lands on /app with the *same* case_id, proving the
  // second POST reused the existing case rather than minting a new
  // one (which would also have burned a second credit).
  await page.waitForURL(/\/app\?case_id=\d+/);
  const url = new URL(page.url());
  const reopenedId = Number(url.searchParams.get("case_id"));
  expect(reopenedId).toBe(firstId);
});
