// ABS-196: "Export reading (PDF)" and "Share with team" buttons in the
// parcel pane must be functional rather than silently inert.
//
// Export:
//   - Disabled until an active session exists (no content to export).
//   - Once a session is live, clicking opens /app/print in a new tab.
//
// Share:
//   - Enabled as soon as a case_id is present in the URL (no session
//     needed — the link points to the case, not the session).
//   - Clicking opens a modal with a "Copy link" button; the modal
//     dismisses on Escape or the close button.

import { expect, openCaseViaApi, test } from "../fixtures/test-env";

const FIRST_MESSAGE = "What is the front yard setback for a residential lot?";

// ── Share modal ──────────────────────────────────────────────────────────────

test.describe("Share with team button", () => {
  test("is enabled when case_id is in URL and opens share modal", async ({
    page,
    authedContext: _,
  }) => {
    const anchorLabel = `abs196-share-${Date.now()}`;
    const { caseId } = await openCaseViaApi({ anchorLabel });

    await page.goto(`/app?case_id=${caseId}`);

    // The parcel pane share button should be visible and NOT disabled.
    const shareBtn = page.getByRole("button", { name: "Share with team" });
    await expect(shareBtn).toBeVisible({ timeout: 8_000 });
    await expect(shareBtn).not.toBeDisabled();

    // Click opens the share modal.
    await shareBtn.click();
    const modal = page.getByRole("dialog", { name: "Share this reading" });
    await expect(modal).toBeVisible();

    // Modal contains a "Copy link" button.
    await expect(
      modal.getByRole("button", { name: /copy link/i }),
    ).toBeVisible();

    // Modal contains the case URL.
    const input = modal.locator("input[readonly]");
    await expect(input).toBeVisible();
    const urlValue = await input.inputValue();
    expect(urlValue).toContain(`case_id=${caseId}`);
  });

  test("share modal closes on Escape", async ({
    page,
    authedContext: _,
  }) => {
    const anchorLabel = `abs196-share-esc-${Date.now()}`;
    const { caseId } = await openCaseViaApi({ anchorLabel });

    await page.goto(`/app?case_id=${caseId}`);

    const shareBtn = page.getByRole("button", { name: "Share with team" });
    await expect(shareBtn).toBeVisible({ timeout: 8_000 });
    await shareBtn.click();

    const modal = page.getByRole("dialog", { name: "Share this reading" });
    await expect(modal).toBeVisible();

    await page.keyboard.press("Escape");
    await expect(modal).not.toBeVisible({ timeout: 3_000 });
  });

  test("share modal closes via close button", async ({
    page,
    authedContext: _,
  }) => {
    const anchorLabel = `abs196-share-close-${Date.now()}`;
    const { caseId } = await openCaseViaApi({ anchorLabel });

    await page.goto(`/app?case_id=${caseId}`);

    const shareBtn = page.getByRole("button", { name: "Share with team" });
    await expect(shareBtn).toBeVisible({ timeout: 8_000 });
    await shareBtn.click();

    const modal = page.getByRole("dialog", { name: "Share this reading" });
    await expect(modal).toBeVisible();

    await modal.getByRole("button", { name: "Close share modal" }).click();
    await expect(modal).not.toBeVisible({ timeout: 3_000 });
  });
});

// ── Export PDF ───────────────────────────────────────────────────────────────

test.describe("Export reading (PDF) button", () => {
  test("is disabled before any chat turn", async ({
    page,
    authedContext: _,
  }) => {
    const anchorLabel = `abs196-export-disabled-${Date.now()}`;
    const { caseId } = await openCaseViaApi({ anchorLabel });

    await page.goto(`/app?case_id=${caseId}`);

    // Without a session the export button should be disabled.
    const exportBtn = page.getByRole("button", {
      name: "Export reading (PDF)",
    });
    await expect(exportBtn).toBeVisible({ timeout: 8_000 });
    await expect(exportBtn).toBeDisabled();
  });

  test("is enabled after a chat turn and opens /app/print in a new tab", async ({
    page,
    authedContext: _,
  }) => {
    const anchorLabel = `abs196-export-enabled-${Date.now()}`;
    const { caseId } = await openCaseViaApi({ anchorLabel });

    // first_message auto-sends a question; the SSE response mints a session.
    await page.goto(
      `/app?case_id=${caseId}&first_message=${encodeURIComponent(FIRST_MESSAGE)}`,
    );

    // Wait for the assistant to respond so the session_id is set.
    await expect(page.getByTestId("chat-thread")).toContainText(
      /bylaw/i,
      { timeout: 20_000 },
    );

    const exportBtn = page.getByRole("button", {
      name: "Export reading (PDF)",
    });
    await expect(exportBtn).not.toBeDisabled({ timeout: 5_000 });

    // Clicking should open /app/print in a new tab.
    const [popup] = await Promise.all([
      page.waitForEvent("popup"),
      exportBtn.click(),
    ]);

    await expect(popup).toHaveURL(/\/app\/print/, { timeout: 8_000 });
  });
});
