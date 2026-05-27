// ABS-57: PDF submission confirmation flow.
//
// Seeds a PDF submission with known low-confidence attributes via
// seed_e2e_submission_pdf.py, navigates to the confirmation page,
// verifies the blocking button is disabled until all red-confidence
// and missing-regulated attributes are addressed, overrides a value,
// confirms, and verifies the evaluator runs and the matrix appears on
// the detail page.

import { execSync } from "node:child_process";
import * as path from "node:path";

import { expect, test } from "../fixtures/test-env";

const TEST_PID = "E2E00100";

let seededSubmissionId: number;

test.describe("pdf-submission-confirm (desktop)", () => {
  test.use({ project: "desktop-chrome" } as never);

  test.beforeAll(() => {
    const repoRoot = path.resolve(__dirname, "..", "..", "..");
    const venvPython = path.join(repoRoot, ".venv", "bin", "python");
    const databaseUrl =
      process.env.DATABASE_URL ||
      "postgresql+psycopg://layer1:layer1@localhost:5432/layer1_test";

    // Ensure bylaw seed exists (idempotent).
    const bylawSeed = path.join(
      repoRoot,
      "scripts",
      "seed_e2e_evaluator_bylaws.py",
    );
    execSync(`"${venvPython}" "${bylawSeed}"`, {
      env: {
        ...process.env,
        DATABASE_URL: databaseUrl,
        PYTHONPATH: `${path.join(repoRoot, "src")}:${process.env.PYTHONPATH || ""}`,
      },
      stdio: "inherit",
    });

    // Seed the PDF submission.
    const pdfSeed = path.join(
      repoRoot,
      "scripts",
      "seed_e2e_submission_pdf.py",
    );
    const output = execSync(`"${venvPython}" "${pdfSeed}"`, {
      env: {
        ...process.env,
        DATABASE_URL: databaseUrl,
        PYTHONPATH: `${path.join(repoRoot, "src")}:${process.env.PYTHONPATH || ""}`,
      },
      encoding: "utf-8",
    });
    // Extract the submission id from stdout: "... seeded submission id=42"
    const match = output.match(/submission id=(\d+)/);
    if (!match) {
      throw new Error(
        `seed_e2e_submission_pdf.py did not output a submission id: ${output}`,
      );
    }
    seededSubmissionId = Number(match[1]);
  });

  test("confirm page shows attributes, gates evaluator, overrides work", async ({
    page,
  }) => {
    // Navigate directly to the confirm page for the seeded submission.
    await page.goto(`/submissions/${seededSubmissionId}/confirm`);

    // Advisory banner visible.
    await expect(page.getByTestId("advisory-only-banner")).toBeVisible();

    // Attributes table renders with the seeded attributes.
    await expect(
      page.getByTestId("confirm-attributes-table"),
    ).toBeVisible();
    await expect(
      page.getByTestId("confirm-row-building_height_m"),
    ).toBeVisible();
    await expect(
      page.getByTestId("confirm-row-building_height_storeys"),
    ).toBeVisible();
    await expect(
      page.getByTestId("confirm-row-gross_floor_area_m2"),
    ).toBeVisible();

    // Confidence badges show correct levels.
    // building_height_m = 0.5 → red (50%)
    await expect(
      page.getByTestId("confidence-badge-building_height_m"),
    ).toContainText("50%");
    // building_height_storeys = 0.85 → yellow (85%)
    await expect(
      page.getByTestId("confidence-badge-building_height_storeys"),
    ).toContainText("85%");
    // gross_floor_area_m2 = 0.3 → red (30%)
    await expect(
      page.getByTestId("confidence-badge-gross_floor_area_m2"),
    ).toContainText("30%");

    // Confirm button should be disabled (red-confidence attributes
    // need addressing).
    const confirmBtn = page.getByTestId("confirm-and-evaluate-button");
    await expect(confirmBtn).toBeDisabled();

    // Blockers panel should be visible.
    await expect(page.getByTestId("confirm-blockers")).toBeVisible();

    // Override the low-confidence building_height_m.
    await page
      .getByTestId("confirm-override-input-building_height_m")
      .fill("10");
    await page
      .getByTestId("confirm-override-button-building_height_m")
      .click();
    // Wait for the save to complete (table refreshes).
    await expect(
      page.getByTestId("confirm-row-building_height_m"),
    ).toBeVisible();

    // Override the low-confidence gross_floor_area_m2.
    await page
      .getByTestId("confirm-override-input-gross_floor_area_m2")
      .fill("500");
    await page
      .getByTestId("confirm-override-button-gross_floor_area_m2")
      .click();
    await expect(
      page.getByTestId("confirm-row-gross_floor_area_m2"),
    ).toBeVisible();

    // Fill in any missing regulated attributes if shown.
    const missingPanel = page.getByTestId("missing-regulated-panel");
    if (await missingPanel.isVisible().catch(() => false)) {
      // Fill each missing input.
      const missingInputs = page.locator("[data-testid^='missing-input-']");
      const count = await missingInputs.count();
      for (let i = 0; i < count; i++) {
        const input = missingInputs.nth(i);
        await input.fill("5");
      }
    }

    // Now the confirm button should be enabled.
    await expect(confirmBtn).toBeEnabled({ timeout: 5_000 });

    // Click confirm + evaluate.
    await confirmBtn.click();

    // Should redirect to /submissions/[id] with the compliance matrix.
    await page.waitForURL(
      new RegExp(`/submissions/${seededSubmissionId}$`),
      { timeout: 30_000 },
    );
    await expect(page.getByTestId("compliance-matrix")).toBeVisible({
      timeout: 30_000,
    });
    await expect(page.getByTestId("overall-status")).toBeVisible();
  });

  test("detail page redirects unconfirmed PDF submissions to confirm", async ({
    page,
  }) => {
    // Seed a fresh PDF submission so it's unconfirmed.
    const repoRoot = path.resolve(__dirname, "..", "..", "..");
    const venvPython = path.join(repoRoot, ".venv", "bin", "python");
    const databaseUrl =
      process.env.DATABASE_URL ||
      "postgresql+psycopg://layer1:layer1@localhost:5432/layer1_test";
    const pdfSeed = path.join(
      repoRoot,
      "scripts",
      "seed_e2e_submission_pdf.py",
    );
    const output = execSync(`"${venvPython}" "${pdfSeed}"`, {
      env: {
        ...process.env,
        DATABASE_URL: databaseUrl,
        PYTHONPATH: `${path.join(repoRoot, "src")}:${process.env.PYTHONPATH || ""}`,
      },
      encoding: "utf-8",
    });
    const match = output.match(/submission id=(\d+)/);
    const freshId = match ? Number(match[1]) : seededSubmissionId;

    // Navigate to detail page.
    await page.goto(`/submissions/${freshId}`);

    // Should redirect to confirm page.
    await page.waitForURL(
      new RegExp(`/submissions/${freshId}/confirm`),
      { timeout: 10_000 },
    );
    await expect(
      page.getByTestId("confirm-attributes-table"),
    ).toBeVisible();
  });
});
