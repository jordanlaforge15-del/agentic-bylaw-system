// Night Manager Mission Console — e2e tests.
//
// The NM UI reads .night-manager/state.json from the filesystem.
// The e2e-up.sh seed step writes a fixture file so these tests run
// against realistic mid-run state without needing a live NM process.

import { test, expect } from "@playwright/test";

const NM_BASE = "/nm";

test.describe("Night Manager — Dashboard", () => {
  test("renders KPI strip and execution plan", async ({ page }) => {
    await page.goto(NM_BASE);
    await expect(page.getByText("NIGHT MANAGER")).toBeVisible();
    await expect(page.getByText("EXECUTION PLAN")).toBeVisible();
    await expect(page.getByText("ACTIVE AGENTS")).toBeVisible();
  });

  test("shows run telemetry in top bar", async ({ page }) => {
    await page.goto(NM_BASE);
    await expect(page.getByText("SYSTEM")).toBeVisible();
    await expect(page.getByText("NOMINAL")).toBeVisible();
    await expect(page.getByText(/nm-\d{8}-\d{4}/)).toBeVisible();
    await expect(page.getByText("OPERATOR")).toBeVisible();
  });

  test("nav bar shows all four tabs", async ({ page }) => {
    await page.goto(NM_BASE);
    await expect(page.getByText("DASHBOARD")).toBeVisible();
    await expect(page.getByText("ISSUE DETAIL")).toBeVisible();
    await expect(page.getByText("LAUNCH")).toBeVisible();
    await expect(page.getByText("REPORTS")).toBeVisible();
  });

  test("footer strip shows health indicators", async ({ page }) => {
    await page.goto(NM_BASE);
    await expect(page.getByText("STATE.JSON")).toBeVisible();
    await expect(page.getByText("LINEAR")).toBeVisible();
    await expect(page.getByText("CLAUDE API")).toBeVisible();
  });

  test("plan slots are clickable links to issue detail", async ({
    page,
  }) => {
    await page.goto(NM_BASE);
    const slot = page.locator('a[href*="/nm/issues/ABS-"]').first();
    await expect(slot).toBeVisible();
    await slot.click();
    await expect(page).toHaveURL(/\/nm\/issues\/ABS-\d+/);
  });

  test("theme switch toggles between themes", async ({ page }) => {
    await page.goto(NM_BASE);
    const apolloBtn = page.getByTitle("APOLLO");
    await expect(apolloBtn).toBeVisible();
    await apolloBtn.click();
    const root = page.locator("#nm-root");
    await expect(root).toHaveAttribute("data-nm-theme", "apollo");
  });
});

test.describe("Night Manager — Issue Detail", () => {
  test("renders issue header with identifier and status", async ({
    page,
  }) => {
    await page.goto(`${NM_BASE}/issues/ABS-92`);
    await expect(page.getByText("ABS-92")).toBeVisible();
    await expect(page.getByText("FAILED")).toBeVisible();
    await expect(
      page.getByText("Add export-to-CSV on invoices table"),
    ).toBeVisible();
  });

  test("shows error panel for failed issues", async ({ page }) => {
    await page.goto(`${NM_BASE}/issues/ABS-92`);
    await expect(page.getByText("E2E TEST FAILURE")).toBeVisible();
    await expect(
      page.getByText(/invoice-export\.spec\.ts timeout/),
    ).toBeVisible();
  });

  test("shows metadata sidebar with ports and branch", async ({
    page,
  }) => {
    await page.goto(`${NM_BASE}/issues/ABS-92`);
    await expect(page.getByText("METADATA")).toBeVisible();
    await expect(page.getByText("54322")).toBeVisible();
    await expect(page.getByText("8092")).toBeVisible();
  });

  test("shows timeline with lifecycle events", async ({ page }) => {
    await page.goto(`${NM_BASE}/issues/ABS-92`);
    await expect(page.getByText("TIMELINE")).toBeVisible();
    await expect(page.getByText("Issue planned")).toBeVisible();
    await expect(page.getByText("Marked failed")).toBeVisible();
  });

  test("back button navigates to dashboard", async ({ page }) => {
    await page.goto(`${NM_BASE}/issues/ABS-90`);
    await page.getByText("← Back").click();
    await expect(page).toHaveURL(NM_BASE);
  });

  test("shows actions panel", async ({ page }) => {
    await page.goto(`${NM_BASE}/issues/ABS-95`);
    await expect(page.getByText("ACTIONS")).toBeVisible();
    await expect(page.getByText(/Re-spawn agent/)).toBeVisible();
    await expect(page.getByText(/SIGTERM agent process/)).toBeVisible();
  });
});

test.describe("Night Manager — Launch", () => {
  test("renders launch configuration form", async ({ page }) => {
    await page.goto(`${NM_BASE}/launch`);
    await expect(page.getByText("LAUNCH CONFIGURATION")).toBeVisible();
    await expect(page.getByText("Max parallel agents")).toBeVisible();
    await expect(page.getByText("Label filter")).toBeVisible();
    await expect(page.getByText("Model")).toBeVisible();
    await expect(page.getByText("Deploy after merge")).toBeVisible();
  });

  test("shows effective command preview", async ({ page }) => {
    await page.goto(`${NM_BASE}/launch`);
    await expect(page.getByText("EFFECTIVE COMMAND")).toBeVisible();
    await expect(
      page.getByText(/start-night-manager\.sh/),
    ).toBeVisible();
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
    const toggle = page.locator(".nm-toggle").first();
    await toggle.click();
    await expect(page.getByText("--deploy")).toHaveCount(0);
  });
});

test.describe("Night Manager — Reports", () => {
  test("renders report archive and current run", async ({ page }) => {
    await page.goto(`${NM_BASE}/reports`);
    await expect(page.getByText("REPORT ARCHIVE")).toBeVisible();
    await expect(
      page.getByText(/Current run/),
    ).toBeVisible();
  });

  test("shows in-progress stats for current run", async ({ page }) => {
    await page.goto(`${NM_BASE}/reports`);
    await expect(
      page.getByText("Night Manager Run"),
    ).toBeVisible();
    await expect(page.getByText("MERGED")).toBeVisible();
    await expect(page.getByText("FAILED")).toBeVisible();
    await expect(page.getByText("IN FLIGHT")).toBeVisible();
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
