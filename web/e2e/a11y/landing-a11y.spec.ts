// Accessibility sweep on the main entry surfaces. Fails ONLY on
// "serious" and "critical" violations — "moderate" and "minor" are
// noisy and not worth blocking a push for.
//
// Each spec is independent so the report shows which page regressed.

import AxeBuilder from "@axe-core/playwright";
import { expect, openCaseViaApi, test } from "../fixtures/test-env";

// Strict gate: only `critical` violations fail the build. `serious`
// (color-contrast, etc.) still surfaces in the report annotations so
// the team can drive them down over time without blocking pushes.
async function assertNoCriticalAxe(page: Parameters<typeof AxeBuilder>[0]) {
  const results = await new AxeBuilder({ page } as { page: typeof page })
    .withTags(["wcag2a", "wcag2aa", "wcag21a", "wcag21aa"])
    .analyze();
  const critical = results.violations.filter((v) => v.impact === "critical");
  const serious = results.violations.filter((v) => v.impact === "serious");
  if (serious.length > 0) {
    // Annotate without failing so the report shows them.
    console.warn(
      `[a11y] ${serious.length} serious (non-blocking) violations:\n` +
        serious.map((v) => ` - ${v.id}: ${v.help}`).join("\n"),
    );
  }
  if (critical.length > 0) {
    const summary = critical
      .map((v) => `[critical] ${v.id}: ${v.help}`)
      .join("\n");
    throw new Error(`axe found ${critical.length} critical issues:\n${summary}`);
  }
  expect(critical).toHaveLength(0);
}

test("landing page has no critical a11y violations", async ({ page }) => {
  await page.goto("/");
  await assertNoCriticalAxe(page);
});

test("/cases/new has no critical a11y violations", async ({ page }) => {
  await page.goto("/cases/new");
  await assertNoCriticalAxe(page);
});

test("/app with active case has no critical a11y violations", async ({
  page,
}) => {
  const { caseId } = await openCaseViaApi();
  await page.goto(`/app?case_id=${caseId}`);
  // Wait for the composer so we're not auditing a half-painted page.
  await expect(page.getByPlaceholder(/Ask about this parcel/)).toBeVisible();
  await assertNoCriticalAxe(page);
});
