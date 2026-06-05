// ABS-272: get_zone_profile thick tool.
//
// Exercises the thick tool over HTTP via the test-only
// ``POST /v1/_test/zone-profile`` endpoint, which calls
// ``RetrievalService.get_zone_profile`` and returns the compact DTO
// projection plus the ``unknown_zone`` flag and citation count.
//
// This spec covers, at the FastAPI ↔ Postgres boundary:
//
//   - a known zone (HR-2) returns a full, citation-backed DTO in ONE
//     call (FR-2.1/2.2/2.4) — height/coverage, permitted +
//     not-permitted uses
//   - representative Regional Centre zones all resolve (AC-2.3)
//   - the ``include`` filter omits unrequested sections (AC-2.6)
//   - an unknown zone returns unknown_zone=true and NO server error
//     (FR-2.5 — graceful, no exception)
//
// Data dependency: the spec seeds its own Regional-Centre-shaped bylaw
// via ``scripts/seed_e2e_zone_profile.py`` (idempotent get-or-create).

import { execSync } from "node:child_process";
import * as path from "node:path";

import { test, expect, E2E_API_URL, DEMO_USER_ID } from "../fixtures/test-env";

test.beforeAll(() => {
  const repoRoot = path.resolve(__dirname, "..", "..", "..");
  const venvPython = path.join(repoRoot, ".venv", "bin", "python");
  const pgPort = process.env.PG_PORT || "5432";
  const databaseUrl =
    process.env.DATABASE_URL ||
    `postgresql+psycopg://layer1:layer1@localhost:${pgPort}/layer1_test`;
  const env = {
    ...process.env,
    DATABASE_URL: databaseUrl,
    PYTHONPATH: `${path.join(repoRoot, "src")}:${path.join(repoRoot, "mcp")}:${process.env.PYTHONPATH || ""}`,
  };
  execSync(
    `"${venvPython}" "${path.join(repoRoot, "scripts", "seed_e2e_zone_profile.py")}"`,
    { env, encoding: "utf-8" },
  );
});

async function zoneProfile(
  request: import("@playwright/test").APIRequestContext,
  zone: string,
  include?: string[],
) {
  return await request.post(`${E2E_API_URL}/v1/_test/zone-profile`, {
    headers: {
      "Content-Type": "application/json",
      "X-Test-User-Id": DEMO_USER_ID,
    },
    data: include ? { zone, include } : { zone },
  });
}

// FR-2.1/2.2/2.4 — one call returns a full, citation-backed DTO.
test("HR-2 returns a full citation-backed DTO in one call", async ({ request }) => {
  const resp = await zoneProfile(request, "HR-2");
  expect(resp.ok()).toBeTruthy();
  const body = await resp.json();

  expect(body.unknown_zone).toBe(false);
  expect(body.citation_count).toBeGreaterThan(0);

  const profile = body.profile;
  expect(profile.zone).toBe("HR-2");
  expect(profile.dimensions.max_height_m).toBe(25.0);
  expect(profile.dimensions.max_lot_coverage_pct).toBe(65.0);
  expect(profile.uses.permitted).toContain("multi-unit dwelling");
  expect(profile.uses.not_permitted).toContain("home occupation");
  expect(Array.isArray(profile.citations)).toBeTruthy();
  expect(profile.citations.length).toBeGreaterThan(0);
});

// AC-2.3 — representative Regional Centre zones all resolve.
for (const zone of ["HR-2", "COR", "HR-1", "CEN-2"]) {
  test(`representative zone ${zone} resolves to a non-null DTO`, async ({ request }) => {
    const resp = await zoneProfile(request, zone);
    expect(resp.ok()).toBeTruthy();
    const body = await resp.json();
    expect(body.unknown_zone).toBe(false);
    expect(body.citation_count).toBeGreaterThan(0);
    expect(body.profile.uses).toBeDefined();
  });
}

// AC-2.6 — include filter omits unrequested sections.
test("include=[dimensions] omits uses and parking", async ({ request }) => {
  const resp = await zoneProfile(request, "HR-2", ["dimensions"]);
  expect(resp.ok()).toBeTruthy();
  const profile = (await resp.json()).profile;
  expect(profile.dimensions).toBeDefined();
  expect(profile.uses).toBeUndefined();
  expect(profile.parking).toBeUndefined();
});

// FR-2.5 — unknown zone is graceful: unknown_zone=true, no server error.
test("unknown zone returns unknown_zone=true without a 500", async ({ request }) => {
  const resp = await zoneProfile(request, "XYZ-99");
  expect(resp.ok()).toBeTruthy();
  const body = await resp.json();
  expect(body.unknown_zone).toBe(true);
  expect(body.citation_count).toBe(0);
  expect(body.profile.unknown_zone).toBe(true);
});
