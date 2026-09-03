// Functional: ABS-453 — the "Case #N" badge in the BalanceStrip must never
// show the internal advisor_case.id, not even for one frame.
//
// The strip used to render `caseNumber ?? caseId`, so a load that arrived
// without ?case_number= painted the raw DB id ("CASE #17") until the case
// list resolved and swapped it for the user-facing number ("CASE #7"). Two
// different numbers in the same element inside one page load is confusing —
// a user who screenshots "case 17" can never find it again.
//
// The fix drops the fallback (the badge hides until the number is known) and
// picks user_case_number up from GET /api/cases, which the workspace already
// fetches for the parcel pane.
//
// This spec widens the vulnerable window deliberately: /api/cases is delayed
// so the pre-resolution state is observable, and the strip is sampled
// continuously across it. Before the fix the very first sample reads
// "CASE #<dbId>".

import {
  DEMO_USER_ID,
  E2E_API_URL,
  expect,
  test,
} from "../fixtures/test-env";

type OpenCaseBody = {
  case: { id: number; user_case_number: number };
};

async function openCase(anchorLabel: string): Promise<OpenCaseBody["case"]> {
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
    throw new Error(`openCase failed: ${res.status} ${await res.text()}`);
  }
  return ((await res.json()) as OpenCaseBody).case;
}

/** Open cases until the DB id and the user-facing number differ — the only
 * configuration in which the two are distinguishable in the UI. The demo
 * user's advisor_case.id is global while user_case_number is per-user, so
 * they diverge almost immediately; the loop just makes it deterministic. */
async function openDivergentCase(
  suffix: string,
): Promise<OpenCaseBody["case"]> {
  let opened = await openCase(`abs453-${suffix}-0`);
  for (let i = 1; i < 5 && opened.id === opened.user_case_number; i += 1) {
    opened = await openCase(`abs453-${suffix}-${i}`);
  }
  expect(
    opened.id,
    "need a case whose DB id differs from its user-facing number",
  ).not.toBe(opened.user_case_number);
  return opened;
}

test("case badge never renders the internal DB id, even before the case list resolves", async ({
  page,
}) => {
  const suffix = `${Date.now()}-${Math.random().toString(36).slice(2, 7)}`;
  const { id: caseId, user_case_number: caseNum } =
    await openDivergentCase(suffix);

  // Hold both load-time sources of user_case_number long enough that the
  // "not yet known" state is observable: the case list and, since ABS-424,
  // the single-case lookup GET /api/cases/<id>. Deeper sub-routes
  // (/api/cases/<id>/close etc.) must still pass through.
  await page.route(
    (url) => /^\/api\/cases(\/\d+)?$/.test(url.pathname),
    async (route) => {
      await new Promise((resolve) => setTimeout(resolve, 3_000));
      await route.continue();
    },
  );

  // Direct URL load with case_id ONLY — the repro. (The /cases/new redirect
  // also passes case_number; sidebar navigation and reloads may not.)
  await page.goto(`/app?case_id=${caseId}`);

  const strip = page.getByTestId("balance-strip");
  await expect(strip).toBeVisible({ timeout: 10_000 });

  // Sample the strip across the delayed window. `\b` keeps "#7" from
  // matching inside "#17" (and vice versa).
  const dbIdBadge = new RegExp(`Case #${caseId}\\b`, "i");
  const correctBadge = new RegExp(`Case #${caseNum}\\b`, "i");
  const deadline = Date.now() + 8_000;
  let sawCorrect = false;
  while (Date.now() < deadline) {
    const text = (await strip.textContent()) ?? "";
    expect(
      text,
      `badge showed the internal DB id (#${caseId}) instead of the user-facing number (#${caseNum})`,
    ).not.toMatch(dbIdBadge);
    if (correctBadge.test(text)) {
      sawCorrect = true;
      break;
    }
    await page.waitForTimeout(100);
  }
  expect(sawCorrect, "badge never settled on the user-facing case number").toBe(
    true,
  );

  // ...and it stays correct once the page is fully settled.
  await expect(strip).toContainText(correctBadge);
  await expect(strip).not.toContainText(dbIdBadge);
});
