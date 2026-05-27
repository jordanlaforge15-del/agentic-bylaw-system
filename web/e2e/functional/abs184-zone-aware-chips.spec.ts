// ABS-184: Suggestion chips above the composer must reflect the anchored
// parcel's zone, not display an identical hardcoded set on every case.
//
// Verifies:
//   1. Without a zone (no parcel resolved yet), generic chips are shown —
//      including "What are the permitted uses here?" and NOT the old
//      hardcoded "Compare to RT-2 limits" which was meaningless for
//      non-residential zones.
//   2. Clicking a chip sends that text as the user's message.

import { expect, openCaseViaApi, test } from "../fixtures/test-env";

// Generic chips expected when no parcel/zone is anchored.
const GENERIC_CHIPS = [
  "What does the yard look like?",
  "Generate a massing study",
  "What permits will I need?",
  "What are the permitted uses here?",
];

// Chip that was hardcoded before this fix — should never appear for a
// generic/no-parcel state.
const OLD_HARDCODED_CHIP = "Compare to RT-2 limits";

test.describe("Zone-aware suggestion chips", () => {
  test("shows generic chips when no parcel is anchored", async ({
    page,
    authedContext: _,
  }) => {
    const { caseId } = await openCaseViaApi({
      anchorLabel: `abs184-generic-${Date.now()}`,
    });
    await page.goto(`/app?case_id=${caseId}`);

    // Composer must be visible before asserting chips.
    await expect(
      page.getByPlaceholder(/Ask about this parcel/),
    ).toBeVisible({ timeout: 8_000 });

    // All generic chips should be visible.
    for (const chip of GENERIC_CHIPS) {
      await expect(
        page.getByRole("button", { name: chip, exact: true }),
      ).toBeVisible();
    }

    // The old hardcoded RT-2 chip must not appear in the generic set.
    await expect(
      page.getByRole("button", { name: OLD_HARDCODED_CHIP, exact: true }),
    ).not.toBeVisible();
  });

  test("clicking a chip sends the chip text as a message", async ({
    page,
    authedContext: _,
  }) => {
    const { caseId } = await openCaseViaApi({
      anchorLabel: `abs184-chip-click-${Date.now()}`,
    });
    await page.goto(`/app?case_id=${caseId}`);

    const textarea = page.getByPlaceholder(/Ask about this parcel/);
    await expect(textarea).toBeVisible({ timeout: 8_000 });

    const targetChip = "What permits will I need?";
    await page.getByRole("button", { name: targetChip, exact: true }).click();

    // Chip click submits the text — the textarea clears and the chat
    // thread gains a user bubble with the chip text.
    await expect(textarea).toHaveValue("");
    await expect(page.getByTestId("chat-thread")).toContainText(
      targetChip,
      { timeout: 5_000 },
    );
  });
});
