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

test("continuing an already-open anchor reuses the same case (no new charge)", async ({
  page,
}) => {
  // Open the case via the API first so we have a known case_id to
  // compare against after the UI flow.
  //
  // ABS-320: the case-open form no longer mints a tier credit on submit —
  // a case is a free container and the form sells priced answers. The
  // reuse path is now the "EXISTING CASE FOUND" banner's "Continue case"
  // button, which navigates straight into the existing case without any
  // POST (and therefore can't double-charge or orphan a credit).
  const anchor = `idem-ui-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`;
  const { caseId: firstId } = await openCaseViaApi({ anchorLabel: anchor });

  await page.goto("/cases/new");
  const anchorInput = page.getByPlaceholder(/1234 Main St, Halifax/);
  await anchorInput.fill(anchor);

  // Blur surfaces the in-window match banner.
  await anchorInput.blur();
  await expect(page.getByText(/EXISTING CASE FOUND/)).toBeVisible();

  await page.getByRole("button", { name: /Continue case/ }).click();

  // Lands on /app with the *same* case_id — the existing case is reused,
  // never re-created.
  await page.waitForURL(/\/app\?case_id=\d+/);
  const url = new URL(page.url());
  const reopenedId = Number(url.searchParams.get("case_id"));
  expect(reopenedId).toBe(firstId);
});
