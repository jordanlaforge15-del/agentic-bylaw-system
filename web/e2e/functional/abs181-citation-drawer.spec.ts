// ABS-181: Citation cards in the "Cited this thread" panel must be
// clickable and open a clause-detail drawer.
//
// Prior behaviour: the cards were plain divs — no cursor, no focus
// ring, no response to click.
//
// New behaviour:
//   - Each card is a <button> (keyboard-accessible, pointer cursor).
//   - Clicking opens a side-drawer showing the clause text and
//     outline path (ancestor chain).
//   - The drawer fetches from /api/citation?citation_path=… and
//     renders the text when the response arrives.
//   - The drawer closes on Escape or the × button.
//
// We seed a synthetic chat session via POST /v1/_test/seed-session
// so the parcel pane renders with citations without requiring real
// bylaw content in the test DB.

import {
  E2E_API_URL,
  DEMO_USER_ID,
  expect,
  openCaseViaApi,
  test,
} from "../fixtures/test-env";

/** Seed a session with location + citation tool results via the test API. */
async function seedSessionWithCitation(caseId: number): Promise<string> {
  const res = await fetch(`${E2E_API_URL}/v1/_test/seed-session`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      "X-Test-User-Id": DEMO_USER_ID,
    },
    body: JSON.stringify({ case_id: caseId }),
  });
  if (!res.ok) {
    throw new Error(
      `seed-session failed: ${res.status} ${await res.text()}`,
    );
  }
  const data = (await res.json()) as { session_id: string };
  return data.session_id;
}

/** Navigate to the case (no first_message) so the restore flow loads
 *  the seeded session, and wait for the "CITED THIS THREAD" panel. */
async function openCaseWithCitations(
  page: import("@playwright/test").Page,
  caseId: number,
) {
  await page.goto(`/app?case_id=${caseId}`);

  // The restore flow fetches /api/chat/sessions (list) and then the
  // specific session; wait for the parcel pane to populate.
  await expect(page.getByText("CITED THIS THREAD")).toBeVisible({
    timeout: 15_000,
  });
}

test.describe("Citation drawer (ABS-181)", () => {
  test("citation cards are buttons with accessible roles", async ({
    page,
    authedContext: _,
  }) => {
    const { caseId } = await openCaseViaApi();
    await seedSessionWithCitation(caseId);
    await openCaseWithCitations(page, caseId);

    // Each citation card must be a <button>, not a plain <div>.
    const citationBtn = page.getByRole("button", { name: /view clause/i });
    await expect(citationBtn.first()).toBeVisible();
  });

  test("clicking a citation card opens the clause drawer", async ({
    page,
    authedContext: _,
  }) => {
    const { caseId } = await openCaseViaApi();
    await seedSessionWithCitation(caseId);
    await openCaseWithCitations(page, caseId);

    // Click the first citation card.
    await page.getByRole("button", { name: /view clause/i }).first().click();

    // Drawer should open.
    const drawer = page.getByRole("dialog", { name: "Clause detail" });
    await expect(drawer).toBeVisible({ timeout: 8_000 });

    // The drawer header must show the citation compact label.
    // (4.2.1 → last segment displayed as-is since it has no schedule prefix)
    await expect(drawer).toContainText("4.2.1");
  });

  test("clause drawer closes on Escape", async ({
    page,
    authedContext: _,
  }) => {
    const { caseId } = await openCaseViaApi();
    await seedSessionWithCitation(caseId);
    await openCaseWithCitations(page, caseId);

    await page.getByRole("button", { name: /view clause/i }).first().click();

    const drawer = page.getByRole("dialog", { name: "Clause detail" });
    await expect(drawer).toBeVisible({ timeout: 8_000 });

    await page.keyboard.press("Escape");
    await expect(drawer).not.toBeVisible({ timeout: 3_000 });
  });

  test("clause drawer closes via × button", async ({
    page,
    authedContext: _,
  }) => {
    const { caseId } = await openCaseViaApi();
    await seedSessionWithCitation(caseId);
    await openCaseWithCitations(page, caseId);

    await page.getByRole("button", { name: /view clause/i }).first().click();

    const drawer = page.getByRole("dialog", { name: "Clause detail" });
    await expect(drawer).toBeVisible({ timeout: 8_000 });

    await drawer.getByRole("button", { name: "Close clause detail" }).click();
    await expect(drawer).not.toBeVisible({ timeout: 3_000 });
  });
});
