// ABS-274 / Phase 4 — the intent-routed bylaw_query mega-tool.
//
// bylaw_query is a composer: the caller declares an `intent` once and the
// server dispatches to the right Phase 2/3 composition (get_zone_profile /
// get_address_profile). This spec exercises that routing over the real
// FastAPI ↔ Postgres boundary via the test-only POST /v1/_test/bylaw-query
// endpoint, covering:
//
//   - zone_feasibility (HR-2) -> one ZoneProfile with dimensions + uses
//   - use_check (HR-2) -> the permitted/not-permitted use lists
//   - dimensional_check (HR-2, proposed height 80) -> ConformanceCheck=fail
//   - address_lookup (100 Robie Street) -> AddressProfile with zone HR-2
//   - an unrecognised intent -> unrecognized_intent=true, NO 500 (FR-4.2)
//
// Data dependencies: the zone-intent tests seed their own Regional-Centre
// bylaw via scripts/seed_e2e_zone_profile.py (scoped by document_id); the
// address_lookup test reuses scripts/seed_e2e_address_profile.py.

import { execSync } from "node:child_process";
import * as path from "node:path";

import { test, expect, E2E_API_URL, DEMO_USER_ID } from "../fixtures/test-env";

let zoneDocumentId: number | null = null;

function repoEnv() {
  const repoRoot = path.resolve(__dirname, "..", "..", "..");
  const pgPort = process.env.PG_PORT || "5433";
  const databaseUrl =
    process.env.DATABASE_URL ||
    `postgresql+psycopg://layer1:layer1@localhost:${pgPort}/layer1_test`;
  return {
    repoRoot,
    venvPython: path.join(repoRoot, ".venv", "bin", "python"),
    env: {
      ...process.env,
      DATABASE_URL: databaseUrl,
      PYTHONPATH: `${path.join(repoRoot, "src")}:${path.join(repoRoot, "mcp")}:${process.env.PYTHONPATH || ""}`,
    },
  };
}

test.beforeAll(() => {
  const { repoRoot, venvPython, env } = repoEnv();
  // Zone-intent corpus (scoped by document_id below).
  const zoneOut = execSync(
    `"${venvPython}" "${path.join(repoRoot, "scripts", "seed_e2e_zone_profile.py")}"`,
    { env, encoding: "utf-8" },
  );
  const m = zoneOut.match(/document=(\d+)/);
  zoneDocumentId = m ? parseInt(m[1], 10) : null;

  // Address-lookup corpus (resolves "100 Robie Street" -> zone HR-2).
  execSync(
    `"${venvPython}" "${path.join(repoRoot, "scripts", "seed_e2e_address_profile.py")}"`,
    { env, stdio: "inherit" },
  );
});

async function bylawQuery(
  request: import("@playwright/test").APIRequestContext,
  body: Record<string, unknown>,
) {
  return await request.post(`${E2E_API_URL}/v1/_test/bylaw-query`, {
    headers: {
      "Content-Type": "application/json",
      "X-Test-User-Id": DEMO_USER_ID,
    },
    data: {
      ...body,
      ...(zoneDocumentId !== null && body.zone !== undefined
        ? { document_id: zoneDocumentId }
        : {}),
    },
  });
}

// FR-4.2 — zone_feasibility composes the full zone profile in one call.
test("zone_feasibility returns dimensions + uses in one composition", async ({
  request,
}) => {
  const resp = await bylawQuery(request, { intent: "zone_feasibility", zone: "HR-2" });
  expect(resp.ok()).toBeTruthy();
  const body = await resp.json();

  expect(body.intent).toBe("zone_feasibility");
  expect(body.unrecognized_intent).toBe(false);

  const profile = body.compact.zone_profile;
  expect(profile.zone).toBe("HR-2");
  expect(profile.dimensions.max_height_m).toBe(25.0);
  expect(profile.uses.permitted).toContain("multi-unit dwelling");
  expect(Array.isArray(body.compact.citations)).toBeTruthy();
  expect(body.compact.citations.length).toBeGreaterThan(0);
});

// FR-4.2 — use_check returns the use lists.
test("use_check returns the permitted/not-permitted use lists", async ({ request }) => {
  const resp = await bylawQuery(request, { intent: "use_check", zone: "HR-2" });
  expect(resp.ok()).toBeTruthy();
  const profile = (await resp.json()).compact.zone_profile;
  expect(profile.uses.permitted).toContain("multi-unit dwelling");
  expect(profile.uses.not_permitted).toContain("home occupation");
});

// FR-4.3 — dimensional_check flags a proposal over the zone maximum.
test("dimensional_check flags an over-max height as fail", async ({ request }) => {
  const resp = await bylawQuery(request, {
    intent: "dimensional_check",
    zone: "HR-2",
    proposed: { height_m: 80 },
  });
  expect(resp.ok()).toBeTruthy();
  const body = await resp.json();

  expect(body.conformance_overall).toBe("fail");
  const check = body.compact.conformance_check;
  const height = check.results.find((r: { attribute: string }) => r.attribute === "height_m");
  expect(height.status).toBe("fail");
  expect(height.limit).toBe(25.0);
});

// FR-4.2/4.4 — address_lookup composes get_address_profile.
test("address_lookup resolves an address into its zone profile", async ({ request }) => {
  const resp = await bylawQuery(request, {
    intent: "address_lookup",
    address: "100 Robie Street",
  });
  expect(resp.ok()).toBeTruthy();
  const body = await resp.json();

  expect(body.intent).toBe("address_lookup");
  const profile = body.compact.address_profile;
  expect(profile.zone).toBe("HR-2");
  expect(profile.height_precinct).toBe("HP-25");
  expect(body.compact.citations.length).toBeGreaterThan(0);
});

// FR-4.2 — an unrecognised intent degrades gracefully (no 500).
test("unknown intent returns unrecognized_intent=true with suggestions", async ({
  request,
}) => {
  const resp = await bylawQuery(request, { intent: "quantum_zoning" });
  expect(resp.ok()).toBeTruthy();
  const body = await resp.json();

  expect(body.unrecognized_intent).toBe(true);
  expect(body.suggested_tools).toContain("search_bylaw_evidence");
  expect(body.suggested_tools).toContain("lookup_citation");
  expect(body.compact.instruction).toBeTruthy();
});
