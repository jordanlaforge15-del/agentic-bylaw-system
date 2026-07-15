// Functional: opening the same anchor twice is idempotent — the second open
// returns the existing case, never a duplicate.
//
// Regression for ABS-8 (Linear): chris.rafuse@gmail.com hit a prod 402
// because two of his three standard credits were stuck in `reserved` state —
// a duplicate POST /v1/cases for the same anchor used to claim a second
// credit against the same case. ABS-382 made opening free (no credit), and
// ABS-385 makes the free "Start the conversation" CTA the surface that POSTs
// /api/cases. Both API- and UI-level re-opens must return the SAME case_id.
//
// Two specs in one file:
//   1. API-level — two direct POST /v1/cases calls return the same case.
//   2. UI-level  — open via API, then re-open via the free CTA with the same
//      anchor; the redirect lands on the same case_id (idempotent reuse).

import {
  DEMO_USER_ID,
  E2E_API_URL,
  expect,
  openCaseViaApi,
  test,
} from "../fixtures/test-env";

type OpenCaseResponseShape = {
  case: { id: number };
  credit_id: number | null;
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
    }),
  });
  if (!res.ok) {
    throw new Error(
      `openCaseRaw(${anchorLabel}) failed: ${res.status} ${await res.text()}`,
    );
  }
  return (await res.json()) as OpenCaseResponseShape;
}

test("duplicate POST /v1/cases for the same anchor returns the same case", async () => {
  // Unique anchor so we don't collide with other parallel workers'
  // 30-day-window matches in the seeded DB.
  const anchor = `idem-api-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`;

  const first = await openCaseRaw(anchor);
  const second = await openCaseRaw(anchor);

  // Same case row both times — the case-match path, working as designed.
  expect(second.case.id).toBe(first.case.id);
  // ABS-382: opening is free, so credit_id is null on both.
  expect(second.credit_id).toBe(first.credit_id);
  // The second call must report the existing-case match — otherwise the
  // frontend would render a "fresh case opened" for a reopen.
  expect(second.reused_existing_case).toBe(true);
});

test("re-opening an anchor via the free CTA reuses the same case", async ({
  page,
}) => {
  // Open the case via the API first so we have a known case_id to compare
  // against after the UI flow.
  const anchor = `idem-ui-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`;
  const { caseId: firstId } = await openCaseViaApi({ anchorLabel: anchor });

  await page.goto("/cases/new");

  // Fill the same anchor + a question and start the (free) conversation.
  await page.getByPlaceholder(/1234 Main St, Halifax/).fill(anchor);
  await page
    .getByPlaceholder(/Ask your question/)
    .fill("Is a duplex allowed here?");
  await page.getByTestId("start-conversation-btn").click();

  // Lands on /app with the *same* case_id — POST /api/cases reused the
  // existing case; it was never re-created.
  await page.waitForURL(/\/app\?case_id=\d+/);
  const url = new URL(page.url());
  const reopenedId = Number(url.searchParams.get("case_id"));
  expect(reopenedId).toBe(firstId);
});
