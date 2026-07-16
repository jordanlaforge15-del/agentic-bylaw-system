// ABS-188: Parcel panel shows the anchored address even when no spatial
// lookup has been performed yet.
//
// Repro: open a case anchored to a property address, navigate to /app
// without sending any chat messages. Before this fix the right pane
// read "No parcel yet." with a generic prompt. After the fix it shows
// the anchor address and a "Geocoding pending" note.

import { expect, openCaseViaApi, test } from "../fixtures/test-env";

const ANCHOR_ADDRESS = "5184 Morris St, Halifax";

test.describe("ABS-188: Parcel panel anchor pending state", () => {
  test("shows anchor address when no spatial lookup has run", async ({
    page,
    authedContext: _,
  }) => {
    const { caseId } = await openCaseViaApi({
      anchorLabel: ANCHOR_ADDRESS,
      anchorKind: "address",
    });
    await page.goto(`/app?case_id=${caseId}`);

    // Composer must be ready before we check the parcel pane.
    await expect(
      page.getByPlaceholder(/Ask about this parcel/),
    ).toBeVisible({ timeout: 8_000 });

    // The parcel pane should show the anchor address, not the generic
    // "No parcel yet" copy.
    const anchorEl = page.getByTestId("parcel-anchor-address");
    await expect(anchorEl).toBeVisible({ timeout: 5_000 });
    await expect(anchorEl).toContainText(ANCHOR_ADDRESS);

    // The generic empty state should NOT be shown.
    await expect(page.getByText("No parcel yet.")).not.toBeVisible();

    // A "pending" / "geocoding" note should be visible.
    await expect(page.getByText(/Geocoding pending/i)).toBeVisible();
  });
});
