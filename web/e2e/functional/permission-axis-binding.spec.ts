// Functional: ABS-278 — Permission-matrix axes are bound to zone/use entities,
// header-bleed is corrected, and a (use, zone) pair resolves to the right cell.
//
// Seeds a permission matrix (via scripts/seed_e2e_axis_binding.py) whose column
// 3 has a positional header and a leaked zone code ("COR") in a data cell, then
// hits /v1/_test/profile-permission-tables — which runs semantic enrichment and
// returns the resulting table_semantic_profile + table_axis_binding rows plus an
// optional (use, zone) -> cell resolution.

import type { APIRequestContext } from "@playwright/test";
import { execSync } from "node:child_process";
import * as path from "node:path";

import { E2E_API_URL, expect, test } from "../fixtures/test-env";


function runSeeds(): void {
  const repoRoot = path.resolve(__dirname, "..", "..", "..");
  const venvPython = path.join(repoRoot, ".venv", "bin", "python");
  const pgPort = process.env.PG_PORT || "5433";
  const databaseUrl =
    process.env.DATABASE_URL ||
    `postgresql+psycopg://layer1:layer1@localhost:${pgPort}/layer1_test`;
  const env = {
    ...process.env,
    DATABASE_URL: databaseUrl,
    PYTHONPATH: `${path.join(repoRoot, "src")}:${process.env.PYTHONPATH || ""}`,
  };
  execSync(
    `"${venvPython}" "${path.join(repoRoot, "scripts", "seed_e2e_axis_binding.py")}"`,
    { env, stdio: "inherit" },
  );
}


type Axis = {
  axis: string;
  index: number;
  entity_type: string;
  canonical_name: string;
  raw_label: string;
  confidence: number;
  review: string | null;
};
type Profile = {
  table_id: number;
  caption: string;
  profile_type: string | null;
  row_axis_type: string | null;
  column_axis_type: string | null;
  value_type: string | null;
  axes: Axis[];
};


test.beforeAll(() => {
  runSeeds();
});


async function profile(request: APIRequestContext, data: object) {
  const response = await request.post(
    `${E2E_API_URL}/v1/_test/profile-permission-tables`,
    { headers: { "Content-Type": "application/json" }, data },
  );
  expect(
    response.status(),
    `profile-permission-tables failed: ${await response.text()}`,
  ).toBe(200);
  return response.json();
}


test("permission matrix gets a permission_matrix profile with use/zone axes", async ({
  request,
}) => {
  const body = await profile(request, { bylaw_name: "Axis Binding Test By-law" });
  expect(body.table_count).toBeGreaterThanOrEqual(1);

  const matrix: Profile = body.profiles[0];
  // AC4: profile exists with the expected axis types.
  expect(matrix.profile_type).toBe("permission_matrix");
  expect(matrix.row_axis_type).toBe("use");
  expect(matrix.column_axis_type).toBe("zone");
  expect(matrix.value_type).toBe("permission_marker");

  // AC1: clean zone-header columns are bound to zone entities matching the token.
  const columns = matrix.axes.filter((a) => a.axis === "column");
  const colByIndex = (i: number) => columns.find((c) => c.index === i);
  expect(colByIndex(1)?.entity_type).toBe("zone");
  expect(colByIndex(1)?.canonical_name).toBe("DD");
  expect(colByIndex(2)?.canonical_name).toBe("DH");

  // AC2: use-phrase rows are bound to use entities.
  const rows = matrix.axes.filter((a) => a.axis === "row");
  const rowNames = rows
    .filter((r) => r.entity_type === "use")
    .map((r) => r.canonical_name);
  expect(rowNames).toContain("restaurant use");
  expect(rowNames).toContain("office use");
});


test("header-bleed zone code is re-attributed to the column axis (low confidence)", async ({
  request,
}) => {
  const body = await profile(request, { bylaw_name: "Axis Binding Test By-law" });
  const matrix: Profile = body.profiles[0];

  // AC3: column 3's header was positional ("3"); the leaked "COR" in its data
  // cell is recovered as a zone binding, flagged for review at low confidence.
  const col3 = matrix.axes.find((a) => a.axis === "column" && a.index === 3);
  expect(col3, "expected a recovered binding for column 3").toBeTruthy();
  expect(col3?.entity_type).toBe("zone");
  expect(col3?.canonical_name).toBe("COR");
  expect(col3?.confidence).toBeLessThanOrEqual(0.6);
  expect(col3?.review).toBe("needs_review");
});


test("a (use, zone) pair resolves to the correct cell — including the bled zone", async ({
  request,
}) => {
  // AC5: clean intersection.
  const clean = await profile(request, {
    bylaw_name: "Axis Binding Test By-law",
    use_name: "Restaurant use",
    zone: "DH",
  });
  expect(clean.resolution).not.toBeNull();
  expect(clean.resolution.row_index).toBe(1);
  expect(clean.resolution.col_index).toBe(2);

  // The recovered (header-bleed) zone is usable for resolution too.
  const bled = await profile(request, {
    bylaw_name: "Axis Binding Test By-law",
    use_name: "Office use",
    zone: "COR",
  });
  expect(bled.resolution).not.toBeNull();
  expect(bled.resolution.row_index).toBe(2);
  expect(bled.resolution.col_index).toBe(3);
});
