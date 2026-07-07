// Functional: ABS-362 — the report letterhead subtitle must match the
// resolved zone, not a hardcoded literal.
//
// The bug: build_report (advisor.billing.report) always fell back to the
// hardcoded DEFAULT_ZONE_SUBTITLE ("Established Residential, Type 1") since
// nothing ever populated purchase.metadata_json["zone_subtitle"] — every
// report's letterhead + title-block subtitle read that literal regardless of
// the parcel's actual zone, contradicting the body's own Zone row (e.g.
// DH-1 for 5184 Morris St).
//
// The fix: build_report now derives zone_subtitle from the report's own
// parsed Zone value (keyvals or table row) when present, falling back to
// the literal only when the body carries no Zone at all. See
// tests/advisor/billing/test_report.py for the derivation unit tests.
//
// This spec drives the real /app/answers view (same stub-at-the-network-
// boundary pattern as the ABS-342/365/366 report specs) with a report whose
// zone_subtitle is a resolved DH-1 zone — matching its own body Zone row —
// and asserts the letterhead subtitle renders that resolved value, not the
// old literal.

import { expect, test } from "../fixtures/test-env";
import type { Page, Route } from "@playwright/test";

type Report = Record<string, unknown>;

function dhZoneReport(): Report {
  return {
    ref: "PU-000362",
    report_type: "Permitted-use determination",
    address: "5184 Morris St",
    zone_subtitle: "DH-1 · Downtown Halifax - 1",
    issued: "2026-07-07",
    prepared_for: "Jordan Buyer",
    bylaw_version: "HRM Regional Centre Land Use By-law — 2024 consolidation",
    price_cents: 9900,
    currency: "CAD",
    verdict: { status: "pass", label: "Permitted as-of-right" },
    summary: "The proposed use is permitted as-of-right at 5184 Morris St.",
    blocks: [
      {
        type: "table",
        title: "Property Summary",
        columns: ["Field", "Value"],
        rows: [
          { cells: ["Zone", "DH-1 (Downtown Halifax - 1)"], status: null },
        ],
      },
    ],
  };
}

type Purchase = {
  id: number;
  question_slug: string;
  status: string;
  price_cents: number;
  currency: string;
  answer: string | null;
  report: Report | null;
  failure_reason: string | null;
  refinement_count: number;
  refinements_remaining: number;
  window_expires_at: string | null;
};

function purchase(overrides: Partial<Purchase> = {}): Purchase {
  return {
    id: 362,
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
    ...overrides,
  };
}

function json(route: Route, body: unknown, status = 200) {
  return route.fulfill({
    status,
    contentType: "application/json",
    body: JSON.stringify(body),
  });
}

async function stubPurchase(page: Page, get: () => unknown) {
  await page.route(
    /\/api\/billing\/questions\/purchases\/\d+(\/(answer|refine))?$/,
    (route) => json(route, get()),
  );
}

test.describe("report subtitle matches the resolved zone (ABS-362)", () => {
  test("title-block subtitle shows the resolved DH-1 zone, matching the body, not the ER-1 default", async ({
    page,
  }) => {
    await stubPurchase(page, () => purchase());
    await page.goto("/app/answers/362");

    const doc = page.getByTestId("report-document");
    await expect(doc).toBeVisible();

    // Body Zone row (Property Summary table).
    const table = page.getByTestId("block-table");
    await expect(table).toContainText("DH-1 (Downtown Halifax - 1)");

    // Title-block subtitle must match the body's zone, not the hardcoded
    // "Established Residential, Type 1" literal.
    const subtitle = page.getByTestId("report-zone");
    await expect(subtitle).toHaveText("DH-1 · Downtown Halifax - 1");
    await expect(subtitle).not.toHaveText(/Established Residential/i);
  });
});
