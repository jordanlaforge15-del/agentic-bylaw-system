// Night Manager Mission Console — e2e tests.
//
// We intercept /api/nm/state with fixture data and abort SSE streams
// so tests are self-contained and tear down cleanly.

import { test, expect, type BrowserContext, type Page } from "@playwright/test";

const NM_BASE = "/nm";

const FIXTURE_STATE = {
  run_id: "nm-20260523-2300",
  started_at: new Date(Date.now() - 3600_000).toISOString(),
  config: { max_agents: 3, label: "Triaged", model: "opus", deploy: true },
  plan: [
    { group: 1, deploy: false, parallel: ["ABS-90", "ABS-91", "ABS-92"] },
    { group: 2, deploy: false, parallel: ["ABS-93", "ABS-94", "ABS-95"] },
    { group: 3, deploy: false, parallel: ["ABS-96", "ABS-97"] },
    { group: 4, deploy: true, parallel: [] },
  ],
  issues: {
    "ABS-90": {
      identifier: "ABS-90", title: "Fix login redirect loop on Safari iOS",
      status: "merged", branch: "agent/ABS-90-safari-login-redirect",
      worktree: "/Users/op/wt/ABS-90",
      ports: { pg: 54320, api: 8090, web: 3090 },
      session_id: null, pid: null, log_file: null,
      attempts: 1, review_attempts: 1,
      started_at: new Date(Date.now() - 3500_000).toISOString(),
      completed_at: new Date(Date.now() - 3200_000).toISOString(),
      merged_at: new Date(Date.now() - 3100_000).toISOString(),
      error: null,
    },
    "ABS-91": {
      identifier: "ABS-91", title: "Pagination cursor off-by-one",
      status: "merged", branch: "agent/ABS-91-orgs-cursor",
      worktree: "/Users/op/wt/ABS-91",
      ports: { pg: 54321, api: 8091, web: 3091 },
      session_id: null, pid: null, log_file: null,
      attempts: 1, review_attempts: 2,
      started_at: new Date(Date.now() - 3400_000).toISOString(),
      completed_at: new Date(Date.now() - 2800_000).toISOString(),
      merged_at: new Date(Date.now() - 2700_000).toISOString(),
      error: null,
    },
    "ABS-92": {
      identifier: "ABS-92", title: "Add export-to-CSV on invoices table",
      status: "failed", branch: "agent/ABS-92-invoices-csv",
      worktree: "/Users/op/wt/ABS-92",
      ports: { pg: 54322, api: 8092, web: 3092 },
      session_id: null, pid: null, log_file: null,
      attempts: 2, review_attempts: 3,
      started_at: new Date(Date.now() - 3300_000).toISOString(),
      completed_at: new Date(Date.now() - 2500_000).toISOString(),
      merged_at: null,
      error: "E2E test failed: invoice-export.spec.ts timeout after 30s.",
    },
    "ABS-93": {
      identifier: "ABS-93", title: "Sentry breadcrumbs missing user context",
      status: "merged", branch: "agent/ABS-93-sentry-user",
      worktree: "/Users/op/wt/ABS-93",
      ports: { pg: 54323, api: 8093, web: 3093 },
      session_id: null, pid: null, log_file: null,
      attempts: 1, review_attempts: 1,
      started_at: new Date(Date.now() - 2000_000).toISOString(),
      completed_at: new Date(Date.now() - 1500_000).toISOString(),
      merged_at: new Date(Date.now() - 1400_000).toISOString(),
      error: null,
    },
    "ABS-94": {
      identifier: "ABS-94", title: "Migrate billing webhook handler",
      status: "reviewing", branch: "agent/ABS-94-billing-idempotent",
      worktree: "/Users/op/wt/ABS-94",
      ports: { pg: 54324, api: 8094, web: 3094 },
      session_id: null, pid: 42194, log_file: null,
      attempts: 1, review_attempts: 2,
      started_at: new Date(Date.now() - 1200_000).toISOString(),
      completed_at: new Date(Date.now() - 600_000).toISOString(),
      merged_at: null, error: null,
    },
    "ABS-95": {
      identifier: "ABS-95", title: "Widget loading state flicker fix",
      status: "in_progress", branch: "agent/ABS-95-widget-flicker",
      worktree: "/Users/op/wt/ABS-95",
      ports: { pg: 54325, api: 8095, web: 3095 },
      session_id: null, pid: 42195, log_file: null,
      attempts: 1, review_attempts: 0,
      started_at: new Date(Date.now() - 800_000).toISOString(),
      completed_at: null, merged_at: null, error: null,
    },
    "ABS-96": {
      identifier: "ABS-96", title: "Stripe metadata to ledger entries",
      status: "in_progress", branch: "agent/ABS-96-stripe-ledger-meta",
      worktree: "/Users/op/wt/ABS-96",
      ports: { pg: 54326, api: 8096, web: 3096 },
      session_id: null, pid: 42196, log_file: null,
      attempts: 1, review_attempts: 0,
      started_at: new Date(Date.now() - 400_000).toISOString(),
      completed_at: null, merged_at: null, error: null,
    },
    "ABS-97": {
      identifier: "ABS-97", title: "Organization-scoped API tokens",
      status: "queued", branch: null, worktree: null, ports: null,
      session_id: null, pid: null, log_file: null,
      attempts: 0, review_attempts: 0,
      started_at: null, completed_at: null, merged_at: null, error: null,
    },
  },
};

async function stubNmApis(context: BrowserContext) {
  await context.route("**/api/nm/state", (route) =>
    route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify(FIXTURE_STATE),
    }),
  );
  await context.route("**/api/nm/reports", (route) =>
    route.fulfill({
      status: 200,
      contentType: "application/json",
      body: "[]",
    }),
  );
  // Abort SSE streams immediately so they don't block teardown
  await context.route("**/api/nm/logs/*/stream", (route) => route.abort());
}

async function gotoNm(page: Page, path = "") {
  await page.goto(`${NM_BASE}${path}`);
  // Wait for client hydration — the nm-app renders after SWR resolves
  await page.waitForSelector(".nm-app", { timeout: 15000 });
}

test.describe("Night Manager — Dashboard", () => {
  test.beforeEach(async ({ context }) => {
    await stubNmApis(context);
  });

  test("renders KPI strip and execution plan", async ({ page }) => {
    await gotoNm(page);
    await expect(page.locator(".nm-topbar__title")).toHaveText("NIGHT MANAGER");
    await expect(page.getByText("EXECUTION PLAN")).toBeVisible();
    await expect(page.getByText("ACTIVE AGENTS")).toBeVisible();
  });

  test("shows run telemetry in top bar", async ({ page }) => {
    await gotoNm(page);
    await expect(
      page.locator(".nm-topbar__stat").filter({ hasText: "SYSTEM" }),
    ).toBeVisible();
    await expect(page.getByText("NOMINAL")).toBeVisible();
    await expect(page.getByText(/nm-\d{8}-\d{4}/)).toBeVisible();
  });

  test("nav bar shows all four tabs", async ({ page }) => {
    await gotoNm(page);
    const nav = page.locator(".nm-nav");
    await expect(nav.getByText("DASHBOARD")).toBeVisible();
    await expect(nav.getByText("ISSUE DETAIL")).toBeVisible();
    await expect(nav.getByText("LAUNCH")).toBeVisible();
    await expect(nav.getByText("REPORTS")).toBeVisible();
  });

  test("footer strip shows health indicators", async ({ page }) => {
    await gotoNm(page);
    const foot = page.locator(".nm-foot");
    await expect(foot.getByText(/STATE\.JSON/)).toBeVisible();
    await expect(foot.getByText(/LINEAR/)).toBeVisible();
  });

  test("plan slots link to issue detail", async ({ page }) => {
    await gotoNm(page);
    const slot = page.locator('a[href*="/nm/issues/ABS-"]').first();
    await expect(slot).toBeVisible();
    await slot.click();
    await expect(page).toHaveURL(/\/nm\/issues\/ABS-\d+/);
  });

  test("theme switch toggles between themes", async ({ page }) => {
    await gotoNm(page);
    await page.getByTitle("APOLLO").click();
    await expect(page.locator("#nm-root")).toHaveAttribute(
      "data-nm-theme",
      "apollo",
    );
  });
});

test.describe("Night Manager — Issue Detail", () => {
  test.beforeEach(async ({ context }) => {
    await stubNmApis(context);
  });

  test("renders failed issue header", async ({ page }) => {
    await gotoNm(page, "/issues/ABS-92");
    // The 22px identifier in the header panel
    const header = page.locator("div").filter({ hasText: /^ABS-92$/ }).first();
    await expect(header).toBeVisible();
    await expect(
      page.getByText("Add export-to-CSV on invoices table"),
    ).toBeVisible();
  });

  test("shows error panel for failed issues", async ({ page }) => {
    await gotoNm(page, "/issues/ABS-92");
    await expect(page.getByText("E2E TEST FAILURE")).toBeVisible();
    await expect(
      page.getByText(/invoice-export\.spec\.ts timeout/),
    ).toBeVisible();
  });

  test("shows metadata sidebar with ports", async ({ page }) => {
    await gotoNm(page, "/issues/ABS-92");
    const meta = page.locator(".nm-kv-grid");
    await expect(meta.getByText("54322")).toBeVisible();
    await expect(meta.getByText("8092")).toBeVisible();
  });

  test("shows timeline with lifecycle events", async ({ page }) => {
    await gotoNm(page, "/issues/ABS-92");
    const timeline = page.locator(".nm-timeline");
    await expect(timeline.getByText("Issue planned")).toBeVisible();
    await expect(timeline.getByText("Marked failed")).toBeVisible();
  });

  test("back button navigates to dashboard", async ({ page }) => {
    await gotoNm(page, "/issues/ABS-90");
    await page.locator(".nm-btn--ghost").filter({ hasText: "Back" }).click();
    await expect(page).toHaveURL(NM_BASE);
  });

  test("shows actions panel", async ({ page }) => {
    await gotoNm(page, "/issues/ABS-95");
    await expect(page.getByText("ACTIONS")).toBeVisible();
    await expect(page.getByText(/Re-spawn agent/)).toBeVisible();
  });
});

test.describe("Night Manager — Launch", () => {
  test("renders launch configuration form", async ({ page }) => {
    await page.goto(`${NM_BASE}/launch`);
    await expect(page.getByText("LAUNCH CONFIGURATION")).toBeVisible();
    await expect(page.getByText("Max parallel agents")).toBeVisible();
    await expect(page.getByText("Label filter", { exact: true })).toBeVisible();
    await expect(page.getByText("Deploy after merge")).toBeVisible();
  });

  test("shows effective command preview", async ({ page }) => {
    await page.goto(`${NM_BASE}/launch`);
    await expect(page.getByText("EFFECTIVE COMMAND")).toBeVisible();
    await expect(page.getByText(/start-night-manager\.sh/).first()).toBeVisible();
  });

  test("shows planned execution groups", async ({ page }) => {
    await page.goto(`${NM_BASE}/launch`);
    await expect(page.getByText("PLANNED EXECUTION")).toBeVisible();
    await expect(page.getByText("G01")).toBeVisible();
  });

  test("shows initiate run button", async ({ page }) => {
    await page.goto(`${NM_BASE}/launch`);
    await expect(
      page.getByRole("button", { name: /INITIATE RUN/ }),
    ).toBeVisible();
  });

  test("deploy toggle changes effective command", async ({ page }) => {
    await page.goto(`${NM_BASE}/launch`);
    await expect(page.getByText("--deploy")).toBeVisible();
    await page.locator(".nm-toggle").first().click();
    await expect(page.getByText("--deploy")).toHaveCount(0);
  });
});

test.describe("Night Manager — Reports", () => {
  test.beforeEach(async ({ context }) => {
    await stubNmApis(context);
  });

  test("renders report archive and current run", async ({ page }) => {
    await gotoNm(page, "/reports");
    await expect(page.getByText("REPORT ARCHIVE")).toBeVisible();
    await expect(page.getByText(/Current run/)).toBeVisible();
  });

  test("shows in-progress stats for current run", async ({ page }) => {
    await gotoNm(page, "/reports");
    await expect(page.getByText("Night Manager Run")).toBeVisible();
    const strip = page.locator(".nm-stats-strip");
    await expect(strip.getByText("MERGED")).toBeVisible();
    await expect(strip.getByText("IN FLIGHT")).toBeVisible();
  });
});

test.describe("Night Manager — No active run", () => {
  test("shows no-run state when state.json missing", async ({
    page,
    context,
  }) => {
    await context.route("**/api/nm/state", (route) =>
      route.fulfill({
        status: 200,
        contentType: "application/json",
        body: "null",
      }),
    );
    await page.goto(NM_BASE);
    await expect(page.getByText("NO ACTIVE RUN")).toBeVisible();
    await expect(
      page.getByRole("link", { name: /NEW RUN/ }),
    ).toBeVisible();
  });
});
