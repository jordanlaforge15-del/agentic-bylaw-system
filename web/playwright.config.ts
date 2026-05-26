// Playwright config for the agentic-bylaw-system UI suite.
//
// Targets four viewport "projects" so a single test file can exercise
// desktop / tablet / mobile-iOS / mobile-Android surfaces:
//
//   * desktop-chrome  — primary product surface (1440x900)
//   * tablet-ipad     — iPad Pro landscape; hits the lg→md break
//   * mobile-iphone   — iPhone 15 portrait; WebKit
//   * mobile-android  — Pixel 7 portrait; Chromium on touch
//
// The smoke suite (e2e/smoke/**) runs across ALL four projects so any
// viewport-only regression surfaces. Functional, visual, and a11y
// suites (e2e/functional, e2e/visual, e2e/a11y) run only on
// `desktop-chrome` — multiplying by viewports there would balloon the
// wall-clock budget for marginal coverage gain.
//
// The full local stack (Postgres test DB + FastAPI on :8001 +
// Next.js on :3001) must already be up — see scripts/e2e-up.sh. We
// deliberately do NOT use Playwright's built-in `webServer` option
// because the stack is multi-process and shared with manual usage; the
// orchestrator script handles lifecycle once for all the projects.

import { defineConfig, devices } from "@playwright/test";

const BASE_URL = process.env.E2E_BASE_URL || "http://localhost:3001";

const isCI = !!process.env.CI;

export default defineConfig({
  testDir: "./e2e",
  // Reseed the demo user's credits + verify the stack is reachable
  // before each run. Idempotent — see web/e2e/global-setup.ts.
  globalSetup: "./e2e/global-setup.ts",
  // Tests don't share state; parallelizing across files is safe and
  // the DB seed gives the demo user enough credits for concurrent
  // cases. If we ever introduce per-user contention, drop this to 1.
  //
  // Local workers capped at 4 (not auto/unlimited): WebKit projects hold
  // an HTTP/1.1 SSE connection open per turn, and Safari's per-origin
  // connection cap is ~6. With >4 concurrent workers the SSE slots can
  // exhaust, queuing React effect fetches behind the stream and pushing
  // `toContainText` past its deadline — the ABS-6 flake. CI already uses
  // 2 workers; 4 local gives enough parallelism without the congestion.
  fullyParallel: true,
  forbidOnly: isCI,
  retries: isCI ? 1 : 0,
  workers: isCI ? 2 : 4,
  reporter: isCI
    ? [["list"], ["html", { open: "never" }]]
    : [["list"], ["html", { open: "never" }]],
  // Per-test 30s cap is generous for SSE flows (chat turn end-to-end
  // is well under 5s with MockGateway). Bump if a real flow needs it.
  timeout: 30_000,
  expect: {
    timeout: 10_000,
    // Generous threshold on screenshot snapshots — we care about
    // "layout collapsed" failures, not single-pixel gradient shifts.
    toHaveScreenshot: { maxDiffPixelRatio: 0.02 },
  },
  use: {
    baseURL: BASE_URL,
    trace: "retain-on-failure",
    screenshot: "only-on-failure",
    video: "retain-on-failure",
    // Tests use this to flag themselves as e2e to the seeded backend.
    // Harmless if the backend doesn't read it.
    extraHTTPHeaders: {
      "X-E2E-Run": "1",
    },
  },
  projects: [
    {
      name: "desktop-chrome",
      use: {
        ...devices["Desktop Chrome"],
        viewport: { width: 1440, height: 900 },
      },
    },
    {
      name: "tablet-ipad",
      testMatch: /e2e\/smoke\//,
      use: {
        ...devices["iPad Pro 11 landscape"],
      },
    },
    {
      name: "mobile-iphone",
      testMatch: /e2e\/(smoke|a11y)\//,
      use: {
        ...devices["iPhone 15"],
      },
    },
    {
      name: "mobile-android",
      testMatch: /e2e\/smoke\//,
      use: {
        ...devices["Pixel 7"],
      },
    },
  ],
});
