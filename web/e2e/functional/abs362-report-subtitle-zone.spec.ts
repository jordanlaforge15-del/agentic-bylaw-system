// Functional: ABS-362 — every surface that shows the report's zone must show
// the RESOLVED zone, not a hardcoded literal.
//
// The bug: build_report (advisor.billing.report) fell back to the hardcoded
// DEFAULT_ZONE_SUBTITLE ("Established Residential, Type 1") for every report,
// because nothing reliably populated the zone. The first fix only mined the
// answer *markdown* blocks — but real answers state the zone free-form (often
// in inline prose block parsing can't lift), so the re-test found the literal
// STILL on the letterhead AND the top nav bar while the body clearly showed
// the resolved DH-1 zone.
//
// The real fix derives zone_subtitle from the engine's own spatial resolution
// (the get_zone_profile / search_bylaw_evidence tool results in the purchase
// transcript — the same source the parcel pane reads), so the resolved value
// travels in the report envelope to EVERY surface that renders it:
//   - the letterhead title-block subtitle (report-zone)
//   - the top nav bar reading string (workspace-label)
//   - the body Zone row
// See tests/advisor/billing/test_report.py for the derivation unit tests.
//
// This spec drives the real /app workspace (same stub-at-the-network-boundary
// pattern as the ABS-361 report specs) with a report whose resolved DH-1 zone
// matches its own body Zone row, and asserts all three surfaces render that
// resolved value — never the old literal.

import { expect, test } from "../fixtures/test-env";
import type { Page, Route } from "@playwright/test";

type Report = Record<string, unknown>;

const REPORT_ID = 6201;
const ADDRESS = "5184 Morris St";
const RESOLVED_ZONE = "DH-1 · Downtown Halifax - 1";
const BODY_ZONE = "DH-1 (Downtown Halifax - 1)";

function dhZoneReport(): Report {
  return {
    ref: "PU-006201",
    report_type: "Permitted-use determination",
    address: ADDRESS,
    // The backend-derived subtitle — the resolved zone, not the ER-1 default.
    zone_subtitle: RESOLVED_ZONE,
    issued: "2026-07-07",
    prepared_for: "Jordan Buyer",
    bylaw_version: "HRM Regional Centre Land Use By-law — 2024 consolidation",
    price_cents: 9900,
    currency: "CAD",
    verdict: { status: "pass", label: "Permitted as-of-right" },
    summary: `The proposed use is permitted as-of-right at ${ADDRESS}.`,
    blocks: [
      {
        type: "table",
        title: "Property Summary",
        columns: ["Field", "Value"],
        rows: [{ cells: ["Zone", BODY_ZONE], status: null }],
      },
    ],
  };
}

function purchase(): Report {
  return {
    id: REPORT_ID,
    question_slug: "permitted_use",
    status: "captured",
    price_cents: 9900,
    currency: "CAD",
    answer: "Raw markdown fallback.",
    report: dhZoneReport(),
    failure_reason: null,
    refinement_count: 0,
    refinements_remaining: 3,
    window_expires_at: "2026-12-31T00:00:00Z",
  };
}

const SIDEBAR_ROW = {
  id: REPORT_ID,
  question_slug: "permitted_use",
  title: "Permitted-use check",
  status: "captured",
  address: ADDRESS,
  zone: "DH-1",
  answer_ready: true,
  updated_at: "2026-07-07T09:00:00Z",
};

function json(route: Route, body: unknown, status = 200) {
  return route.fulfill({
    status,
    contentType: "application/json",
    body: JSON.stringify(body),
  });
}

async function stubWorkspace(page: Page) {
  await page.route(/\/api\/chat\/sessions(\?.*)?$/, (route) =>
    json(route, { sessions: [] }),
  );
  await page.route(/\/api\/billing\/questions\/purchases$/, (route) =>
    json(route, { reports: [SIDEBAR_ROW] }),
  );
  await page.route(
    /\/api\/billing\/questions\/purchases\/\d+(\/(answer|refine))?$/,
    (route) => json(route, purchase()),
  );
}

test.describe("report zone subtitle matches the resolved zone (ABS-362)", () => {
  test("letterhead, top nav bar, and body all show the resolved DH-1 zone — never the ER-1 default", async ({
    page,
  }) => {
    await stubWorkspace(page);
    await page.goto("/app");

    // Open the report from the sidebar (the surface the re-test exercised).
    await page
      .locator("aside")
      .first()
      .getByTestId("case-row")
      .filter({ hasText: ADDRESS })
      .click();

    const doc = page.getByTestId("report-document");
    await expect(doc).toBeVisible();

    // Body Zone row (Property Summary table) — the source of truth.
    await expect(page.getByTestId("block-table")).toContainText(BODY_ZONE);

    // Letterhead title-block subtitle must match the body's zone, not the
    // hardcoded "Established Residential, Type 1" literal.
    const subtitle = page.getByTestId("report-zone");
    await expect(subtitle).toHaveText(RESOLVED_ZONE);
    await expect(subtitle).not.toHaveText(/Established Residential/i);

    // The re-test flagged the top nav bar carrying the same hardcoded string.
    // It reads the same resolved zone subtitle, so it must show DH-1 too.
    const workspaceLabel = page.getByTestId("workspace-label");
    await expect(workspaceLabel).toContainText(RESOLVED_ZONE);
    await expect(workspaceLabel).not.toContainText(/Established Residential/i);
  });
});
