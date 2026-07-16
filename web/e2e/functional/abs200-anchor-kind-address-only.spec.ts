// ABS-200: DA and Project-reference anchor types have no backing data
// source in beta — they were hidden from the dropdown to prevent users
// from creating cases that immediately ask for an address anyway.
//
// This spec asserts:
//   * The anchor-kind <select> offers exactly one option ("Property address").
//   * "Development application" and "Project reference" are NOT present.

import { expect, test } from "../fixtures/test-env";

test("anchor-kind dropdown offers only Property address in beta", async ({
  page,
}) => {
  await page.goto("/cases/new");

  const select = page.getByRole("combobox", { name: /anchor kind/i });
  await expect(select).toBeVisible();

  const options = await select.locator("option").allTextContents();

  expect(options).toEqual(["Property address"]);
  expect(options).not.toContain("Development application");
  expect(options).not.toContain("Project reference");
});
