// ABS-451: citation references written inline in an agent reply — in
// prose and inside attribute-table cells — must open the same clause
// drawer as the right rail's "CITED THIS THREAD" cards.
//
// Prior behaviour: only the rail cards were clickable (ABS-181). The
// identical citation rendered inside the message body was plain text,
// so the reader verifying a number in a dense table had to cross-
// reference the rail to reach the clause.
//
// New behaviour:
//   - An inline reference that resolves to a citation the agent actually
//     retrieved this thread renders as a <button> and opens the clause
//     drawer.
//   - Prose label forms are matched against stored citation paths, so
//     "(Schedule 15)" reaches "SCHEDULES.SCHEDULE_15".
//   - A citation-shaped reference the thread never retrieved stays plain
//     text — no link styling, no dead click target.
//   - Rail card and inline reference share one drawer instance.
//
// Sessions are seeded via POST /v1/_test/seed-session, which accepts the
// final assistant turn's markdown so we can plant a table + prose without
// depending on what the model happens to write.

import {
  E2E_API_URL,
  DEMO_USER_ID,
  expect,
  openCaseViaApi,
  test,
} from "../fixtures/test-env";

/** Reply carrying the same citation twice — once in a table cell, once
 *  in prose — plus one reference that was never retrieved. */
const SECTION_ANSWER = [
  "| Attribute | Value |",
  "| --- | --- |",
  "| Max spaces | 5 (Section 4.2.1) |",
  "",
  "The front yard setback is 3 m (Section 4.2.1).",
  "",
  "We did not retrieve (Section 999) for this parcel.",
].join("\n");

const SCHEDULE_ANSWER = "Maximum streetwall height is set by (Schedule 15).";

async function seedSession(
  caseId: number,
  opts: { citationPath?: string; assistantText?: string } = {},
): Promise<string> {
  const res = await fetch(`${E2E_API_URL}/v1/_test/seed-session`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      "X-Test-User-Id": DEMO_USER_ID,
    },
    body: JSON.stringify({
      case_id: caseId,
      ...(opts.citationPath ? { citation_path: opts.citationPath } : {}),
      ...(opts.assistantText ? { assistant_text: opts.assistantText } : {}),
    }),
  });
  if (!res.ok) {
    throw new Error(`seed-session failed: ${res.status} ${await res.text()}`);
  }
  const data = (await res.json()) as { session_id: string };
  return data.session_id;
}

/** Open the case and wait for the restored transcript + parcel pane. */
async function openSeededCase(
  page: import("@playwright/test").Page,
  caseId: number,
) {
  await page.goto(`/app?case_id=${caseId}`);
  await expect(page.getByText("CITED THIS THREAD")).toBeVisible({
    timeout: 15_000,
  });
}

test.describe("Inline citation references (ABS-451)", () => {
  test("citations in a table cell and in prose are both buttons", async ({
    page,
    authedContext: _,
  }) => {
    const { caseId } = await openCaseViaApi();
    await seedSession(caseId, { assistantText: SECTION_ANSWER });
    await openSeededCase(page, caseId);

    const inline = page.getByTestId("inline-citation");
    await expect(inline).toHaveCount(2);

    // One of them lives inside the attribute table's cell — the exact
    // spot the ticket reports as dead text.
    const inCell = page.locator('td [data-testid="inline-citation"]');
    await expect(inCell).toHaveCount(1);
    await expect(inCell).toHaveText("Section 4.2.1");
    await expect(inCell).toHaveAttribute("data-citation", "4.2.1");

    // Both resolve to the retrieved clause.
    await expect(
      page.getByRole("button", { name: "View cited clause Section 4.2.1" }),
    ).toHaveCount(2);
  });

  test("clicking an inline citation in a table cell opens the clause drawer", async ({
    page,
    authedContext: _,
  }) => {
    const { caseId } = await openCaseViaApi();
    await seedSession(caseId, { assistantText: SECTION_ANSWER });
    await openSeededCase(page, caseId);

    await page.locator('td [data-testid="inline-citation"]').click();

    const drawer = page.getByRole("dialog", { name: "Clause detail" });
    await expect(drawer).toBeVisible({ timeout: 8_000 });
    await expect(drawer).toContainText("4.2.1");

    await page.keyboard.press("Escape");
    await expect(drawer).not.toBeVisible({ timeout: 3_000 });
  });

  test("a citation the thread never retrieved stays plain text", async ({
    page,
    authedContext: _,
  }) => {
    const { caseId } = await openCaseViaApi();
    await seedSession(caseId, { assistantText: SECTION_ANSWER });
    await openSeededCase(page, caseId);

    // The prose is rendered…
    await expect(
      page.getByText("We did not retrieve (Section 999)"),
    ).toBeVisible();
    // …but "Section 999" is not dressed up as a link.
    await expect(
      page.getByTestId("inline-citation").filter({ hasText: "999" }),
    ).toHaveCount(0);
  });

  test("a prose label resolves to its stored citation path", async ({
    page,
    authedContext: _,
  }) => {
    const { caseId } = await openCaseViaApi();
    await seedSession(caseId, {
      citationPath: "SCHEDULES.SCHEDULE_15",
      assistantText: SCHEDULE_ANSWER,
    });
    await openSeededCase(page, caseId);

    const inline = page.getByTestId("inline-citation");
    await expect(inline).toHaveCount(1);
    await expect(inline).toHaveText("Schedule 15");
    await expect(inline).toHaveAttribute(
      "data-citation",
      "SCHEDULES.SCHEDULE_15",
    );

    await inline.click();
    const drawer = page.getByRole("dialog", { name: "Clause detail" });
    await expect(drawer).toBeVisible({ timeout: 8_000 });
    await expect(drawer).toContainText("SCHEDULES.SCHEDULE_15");
  });

  test("inline reference and rail card open the same single drawer", async ({
    page,
    authedContext: _,
  }) => {
    const { caseId } = await openCaseViaApi();
    await seedSession(caseId, { assistantText: SECTION_ANSWER });
    await openSeededCase(page, caseId);

    const drawer = page.getByRole("dialog", { name: "Clause detail" });

    // Rail card first (the ABS-181 path), then close.
    await page.getByRole("button", { name: /view clause/i }).first().click();
    await expect(drawer).toBeVisible({ timeout: 8_000 });
    await drawer.getByRole("button", { name: "Close clause detail" }).click();
    await expect(drawer).toHaveCount(0);

    // Then the inline reference — same drawer, exactly one instance.
    await page.locator('td [data-testid="inline-citation"]').click();
    await expect(drawer).toHaveCount(1);
    await expect(drawer).toContainText("4.2.1");
  });
});
