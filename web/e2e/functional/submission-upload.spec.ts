// ABS-53: submission upload → extraction → evaluator → matrix.
//
// Gating spec for In Review. Seeds the parcel + bylaw fragments via
// scripts/seed_e2e_evaluator_bylaws.py (PID E2E00100), uploads the
// synthetic IFC fixture written by global-setup, asserts the
// extraction surfaced expected attributes, clicks "Run evaluator",
// and asserts the compliance matrix renders.
//
// Single test, single project (desktop-chrome only). The mobile
// projects are covered by the smoke matrix; piling on more here
// would inflate CI time without adding signal.

import { execSync } from "node:child_process";
import * as path from "node:path";

import { expect, test } from "../fixtures/test-env";
import { resolveDatabaseUrl } from "../helpers/database-url";

const TEST_PID = "E2E00100";

test.describe("submission-upload (desktop)", () => {
  test.use({ project: "desktop-chrome" } as never);

  test.beforeAll(() => {
    const repoRoot = path.resolve(__dirname, "..", "..", "..");
    const seed = path.join(
      repoRoot,
      "scripts",
      "seed_e2e_evaluator_bylaws.py",
    );
    const venvPython = path.join(repoRoot, ".venv", "bin", "python");
    // ABS-207: honor PG_PORT for the parallel-worktree case.
    const databaseUrl = resolveDatabaseUrl();
    execSync(`"${venvPython}" "${seed}"`, {
      env: {
        ...process.env,
        DATABASE_URL: databaseUrl,
        PYTHONPATH: `${path.join(repoRoot, "src")}:${process.env.PYTHONPATH || ""}`,
      },
      stdio: "inherit",
    });
  });

  test("upload IFC → see attributes + disclaimer → evaluator → matrix", async ({
    page,
  }) => {
    const repoRoot = path.resolve(__dirname, "..", "..", "..");
    const ifcPath = path.join(
      repoRoot,
      "web",
      "e2e",
      "fixtures",
      "submission-demo.ifc",
    );

    await page.goto("/submissions/new");

    // Disclaimer is on the upload page.
    await expect(page.getByTestId("advisory-only-banner")).toBeVisible();

    // Upload the IFC + parcel PID.
    await page
      .getByTestId("submission-file-input")
      .setInputFiles(ifcPath);
    await page
      .getByTestId("submission-parcel-input")
      .fill(TEST_PID);
    await page.getByTestId("submission-upload-submit").click();

    // Should redirect to /submissions/[id].
    await page.waitForURL(/\/submissions\/\d+$/);

    // Disclaimer reappears on the detail page (per the spec — every
    // page that surfaces extracted attrs / evaluator output shows it).
    await expect(page.getByTestId("advisory-only-banner")).toBeVisible();

    // Attributes table renders with at least the IFC-extracted ones.
    await expect(page.getByTestId("attributes-table")).toBeVisible();
    await expect(
      page.getByTestId("attribute-row-building_height_m"),
    ).toBeVisible();
    await expect(
      page.getByTestId("attribute-row-building_height_storeys"),
    ).toBeVisible();
    await expect(
      page.getByTestId("attribute-row-gross_floor_area_m2"),
    ).toBeVisible();

    // Confidence badges visible — at least one rendered (any confidence level).
    const badges = page.getByTestId("confidence-badge");
    await expect(badges.first()).toBeVisible();

    // Click "Run evaluator".
    await page.getByTestId("run-evaluator-button").click();

    // Matrix appears once the evaluator round-trip completes.
    await expect(page.getByTestId("compliance-matrix")).toBeVisible({
      timeout: 30_000,
    });
    await expect(page.getByTestId("overall-status")).toBeVisible();
  });
});
