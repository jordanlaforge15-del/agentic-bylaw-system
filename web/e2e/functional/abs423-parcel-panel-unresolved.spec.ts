// ABS-423: the Parcel panel promised "Geocoding pending" forever even
// after the backend had already stored a terminal
// ``spatial_facts: {status: "unresolved", reason: ...}`` for the case.
//
// Repro: open a case whose anchor can't be resolved to a parcel (an
// anchor the extractor can't even parse as a civic address or PID is
// the deterministic case — it never touches the geocoder), then load
// /app for that case. Before the fix the pane read "Geocoding pending —
// ask a bylaw question…"; after it says we couldn't locate the address,
// with the reason.
//
// The status rides on GET /v1/cases as ``spatial_status`` /
// ``spatial_reason`` (narrow projections of ``metadata_json`` — the
// full blob deliberately stays off the wire).

import { expect, openCaseViaApi, test } from "../fixtures/test-env";

// No civic number, no PID → extract_location_references returns nothing
// → status=unresolved, reason="could not parse anchor as a civic address
// or PID". Independent of which spatial datasets happen to be ingested.
const UNRESOLVABLE_ANCHOR = "Somewhere Nowhere Nonexistent";

test.describe("ABS-423: Parcel panel unresolved state", () => {
  test("surfaces the failure instead of an eternal pending note", async ({
    page,
    authedContext: _,
  }) => {
    const { caseId } = await openCaseViaApi({
      anchorLabel: UNRESOLVABLE_ANCHOR,
      anchorKind: "address",
    });

    // The API must report the terminal status — this is the contract the
    // pane renders from.
    const res = await page.request.get("/api/cases");
    expect(res.ok()).toBeTruthy();
    const body = (await res.json()) as {
      cases: Array<{
        id: number;
        spatial_status?: string | null;
        spatial_reason?: string | null;
      }>;
    };
    const matched = body.cases.find((c) => c.id === caseId);
    expect(matched, `case ${caseId} missing from GET /api/cases`).toBeTruthy();
    expect(matched?.spatial_status).toBe("unresolved");
    expect(matched?.spatial_reason).toContain("civic address");

    await page.goto(`/app?case_id=${caseId}`);
    await expect(
      page.getByPlaceholder(/Ask about this parcel/),
    ).toBeVisible({ timeout: 8_000 });

    // The anchor still renders…
    const anchorEl = page.getByTestId("parcel-anchor-address");
    await expect(anchorEl).toBeVisible({ timeout: 8_000 });
    await expect(anchorEl).toContainText(UNRESOLVABLE_ANCHOR);

    // …but the copy is the honest failure, with the humanized reason.
    const unresolved = page.getByTestId("parcel-unresolved");
    await expect(unresolved).toBeVisible();
    await expect(unresolved).toContainText(
      /couldn.t locate this address in the parcel data/i,
    );
    await expect(unresolved).toContainText(
      /civic address or PID/i,
    );

    // The eternal-pending promise is gone.
    await expect(page.getByText(/Geocoding pending/i)).not.toBeVisible();
  });
});
