// ABS-493: zone-profile confidence is an ordinal EVIDENCE CLASS, and the gate
// derived from it is independent of how many words the internal query happens
// to contain.
//
// The bug this covers, over the real FastAPI ↔ Postgres boundary: confidence
// used to be `min(1.0, score / 40.0)` compared against 0.5, and
// `_score_fragment` awards a fixed bonus PER MATCHING QUERY TOKEN. The zone
// code is templated into each of get_zone_profile's five internal queries, so
// the gate moved with the code's tokenization instead of with the evidence.
// Every zone in the seeded RC-LUB corpus carries the identical
// `Table 3 > <zone>` setback row, yet COR — no hyphen to split, so two query
// tokens where CEN-2 has four — scored 0.425 and had all three setbacks
// dropped, while CEN-2 scored 1.0 and kept them.
//
// Asserted here:
//   - every representative zone reports setbacks off that shared row, at the
//     SAME rung (0.8, path_anchored) — equal evidence, equal outcome
//   - the rungs served are ladder values, never arbitrary floats
//   - prose that states the query verbatim still clears the gate at
//     body_phrase (0.4) — the parking rule, whose citation path says nothing
//     about parking
//   - the gate still DROPS below threshold: a gated field is absent from both
//     the dimensions object and the confidence map, and is cited by nothing
//     (AC-2.9's instinct, which ABS-493 preserves)
//
// Definition: docs/decisions/ABS-493-CONFIDENCE-DEFINITION.md
// Data dependency: the unified RC-LUB e2e document seeded via
// `scripts/seed_e2e_rclub_unified.py` (ABS-433, idempotent get-or-create).

import { execSync } from "node:child_process";
import * as path from "node:path";

import { test, expect, E2E_API_URL, DEMO_USER_ID } from "../fixtures/test-env";

// The evidence ladder, mirroring EVIDENCE_CLASS_CONFIDENCE in
// mcp/bylaw_retrieval/retrieval/schemas.py. These are ORDINAL RUNGS, not
// probabilities — the spec compares them to each other and to the gate.
const RUNG = {
  exact_path: 1.0,
  bound_table_cell: 0.9,
  path_anchored: 0.8,
  labelled_row: 0.6,
  body_phrase: 0.4,
  body_terms: 0.2,
  no_match: 0.0,
} as const;
const GATE = RUNG.body_phrase;

const SETBACK_FIELDS = [
  "front_setback_m",
  "side_setback_m",
  "rear_setback_m",
] as const;

let zoneDocumentId: number | null = null;

test.beforeAll(() => {
  const repoRoot = path.resolve(__dirname, "..", "..", "..");
  const venvPython = path.join(repoRoot, ".venv", "bin", "python");
  const pgPort = process.env.PG_PORT || "5433";
  const databaseUrl =
    process.env.DATABASE_URL ||
    `postgresql+psycopg://layer1:layer1@localhost:${pgPort}/layer1_test`;
  const env = {
    ...process.env,
    DATABASE_URL: databaseUrl,
    PYTHONPATH: `${path.join(repoRoot, "src")}:${path.join(repoRoot, "mcp")}:${process.env.PYTHONPATH || ""}`,
  };
  const output = execSync(
    `"${venvPython}" "${path.join(repoRoot, "scripts", "seed_e2e_rclub_unified.py")}"`,
    { env, encoding: "utf-8" },
  );
  const m = output.match(/"document_id": (\d+)/);
  zoneDocumentId = m ? parseInt(m[1], 10) : null;
});

async function zoneProfile(
  request: import("@playwright/test").APIRequestContext,
  zone: string,
  include?: string[],
) {
  const resp = await request.post(`${E2E_API_URL}/v1/_test/zone-profile`, {
    headers: {
      "Content-Type": "application/json",
      "X-Test-User-Id": DEMO_USER_ID,
    },
    data: {
      zone,
      ...(include ? { include } : {}),
      ...(zoneDocumentId !== null ? { document_id: zoneDocumentId } : {}),
    },
  });
  expect(resp.ok()).toBeTruthy();
  return (await resp.json()).profile;
}

// The headline invariant. "COR setback" tokenizes to 2 terms and
// "CEN-2 setback" to 4; both hit a `Table 3 > <zone>` row. Equal evidence must
// now mean equal gating outcome AND an equal rung.
test("zones with the same setback-row evidence gate the same regardless of query token count", async ({
  request,
}) => {
  const zones = ["HR-2", "HR-1", "COR", "CEN-2"];
  const rungsPerZone: Record<string, number[]> = {};

  for (const zone of zones) {
    const profile = await zoneProfile(request, zone, ["dimensions"]);
    const rungs: number[] = [];
    for (const field of SETBACK_FIELDS) {
      expect(
        profile.dimensions?.[field],
        `${zone}.${field} was gated out despite a Table 3 > ${zone} row`,
      ).not.toBeUndefined();
      const rung = profile.confidence?.[field];
      expect(rung, `${zone}.${field} has a value but no confidence rung`).toBe(
        RUNG.path_anchored,
      );
      rungs.push(rung);
    }
    rungsPerZone[zone] = rungs;
  }

  // Not merely "all above threshold" — identical evidence, identical rungs.
  const distinct = new Set(zones.map((z) => JSON.stringify(rungsPerZone[z])));
  expect(
    distinct.size,
    `identical evidence produced differing rungs: ${JSON.stringify(rungsPerZone)}`,
  ).toBe(1);
});

// Every served confidence is a rung on the documented ladder, at or above the
// gate. A value off-ladder means something reintroduced a computed float.
test("every served confidence is a documented rung at or above the gate", async ({
  request,
}) => {
  const ladder = Object.values(RUNG);
  for (const zone of ["HR-2", "COR", "CEN-2"]) {
    const profile = await zoneProfile(request, zone);
    const confidence = profile.confidence ?? {};
    expect(Object.keys(confidence).length).toBeGreaterThan(0);
    for (const [field, rung] of Object.entries(confidence)) {
      expect(ladder, `${zone}.${field} served an off-ladder confidence ${rung}`).toContain(
        rung,
      );
      expect(rung as number).toBeGreaterThanOrEqual(GATE);
    }
  }
});

// Structural anchoring is not the only way to clear the gate. The Part V
// parking rule is prose whose citation path ("Part V > 120") says nothing
// about parking; it passes at body_phrase because the section states the query
// verbatim. Pinning the rung keeps a future tightening from silently
// discarding prose-stated answers.
test("prose stating the query verbatim clears the gate at the body_phrase rung", async ({
  request,
}) => {
  const profile = await zoneProfile(request, "HR-2", ["parking"]);

  expect(profile.parking?.min_spaces_per_dwelling_unit).toBe(1);
  expect(profile.confidence?.parking).toBe(RUNG.body_phrase);

  const backed = (profile.citations ?? []).flatMap(
    (c: { backs?: string[] }) => c.backs ?? [],
  );
  expect(backed).toContain("parking");
});

// The gate's instinct, unchanged by ABS-493: below threshold the value is
// dropped AND nothing cites it. COR/CEN-2 heights are governed by Schedule 15
// with no inline number, so max_height_m is absent — and absent means absent
// from the confidence map and from every citation's `backs`, not served with
// a low number attached.
test("a field the profile does not stand behind is dropped, unscored and uncited", async ({
  request,
}) => {
  const profile = await zoneProfile(request, "COR");

  expect(profile.dimensions?.max_height_m).toBeUndefined();
  expect(profile.confidence?.max_height_m).toBeUndefined();

  const backed = (profile.citations ?? []).flatMap(
    (c: { backs?: string[] }) => c.backs ?? [],
  );
  expect(backed).not.toContain("max_height_m");
  // The rest of the row still came through — this is a per-field gate, not a
  // whole-fragment one.
  expect(profile.dimensions?.max_lot_coverage_pct).toBe(70);
});
