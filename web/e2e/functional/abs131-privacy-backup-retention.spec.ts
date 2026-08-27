// Functional: the privacy policy's retention section describes the backups
// that actually run (ABS-131).
//
// Before ABS-131, §5.0 told readers "during beta our backups are manual and
// short-lived" and declined to give a purge horizon. Automating the backups
// made both halves of that false: they are nightly, encrypted, mirrored to a
// separate offsite store, and kept for up to four weeks. A deletion request
// cannot reach a backup that was already written, so the retention window is
// now the honest answer to "when is my deleted data really gone?" — which is
// a disclosure, not a detail.
//
// This spec is the tripwire for the two ways that drifts back out of true:
// someone changes BYLAW_KEEP_WEEKLY without touching the page, or someone
// edits the page back toward the old "manual" language. It asserts the
// horizon the scripts actually implement (7 daily + 4 weekly ≈ one month,
// per docs/PROD_DB_BACKUP.md) and that the retired claim is gone.

import { expect, test } from "../fixtures/test-env";

test.beforeEach(async ({ page }) => {
  await page.goto("/privacy", { waitUntil: "domcontentloaded" });
});

test("privacy §5.0 discloses the real backup retention horizon", async ({
  page,
}) => {
  const section = page.locator("#pv-5");
  await expect(section).toBeVisible();

  const text = (await section.innerText()).replace(/\s+/g, " ");

  // Automated, not hand-run.
  expect(text).toMatch(/automatically each night/i);
  // Encrypted, and somewhere other than the live server.
  expect(text).toMatch(/encrypted/i);
  expect(text).toMatch(/separate storage facility/i);
  // The horizon the rotation actually implements: 7 daily + 4 weekly.
  expect(text).toMatch(/seven daily and four weekly/i);
  expect(text).toMatch(/one month/i);
});

test("privacy §5.0 no longer claims backups are manual and short-lived", async ({
  page,
}) => {
  const body = (await page.locator("main").innerText()).replace(/\s+/g, " ");

  expect(body).not.toMatch(/backups are manual/i);
  expect(body).not.toMatch(/short-lived/i);
  // The old text also declined to publish any horizon at all.
  expect(body).not.toMatch(/do not currently publish a backup-purge SLA/i);
});

test("privacy §5.0 keeps deletion and backup persistence in the same breath", async ({
  page,
}) => {
  const text = (await page.locator("#pv-5").innerText()).replace(/\s+/g, " ");

  // A reader who is told deletion removes a thread must, in the same
  // section, be told the backup copy outlives it.
  expect(text).toMatch(/removes it from the live database/i);
  expect(text).toMatch(/deleted data can persist in those backups/i);
});

test("the EU data-location claim still covers where backups sit", async ({
  page,
}) => {
  const text = (await page.locator("#pv-5").innerText()).replace(/\s+/g, " ");

  // Backups leave the application server. If the offsite store were outside
  // the EU, §5.0's location claim would be wrong — so the page must say the
  // backup store is in the EU too, not only the database server.
  const euClaims = text.match(/European Union/gi) ?? [];
  expect(euClaims.length).toBeGreaterThanOrEqual(2);
});
