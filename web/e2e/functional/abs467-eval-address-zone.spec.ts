// ABS-467: eval cases are anchored on addresses that confirm their zone
//
// Every case in evals/regional_centre_test_prompts.json carries a `zone` and
// an `address`. Those used to be two independent operator inputs, and nothing
// resolved one against the other: 17 of 20 disagreed — 7 landed in a
// different zone, 10 landed outside every mapped boundary. A case claiming
// CEN-1 whose address is in DH grades a correct DH answer against CEN-1
// expectations, so it marks right answers wrong and can hide a real
// regression behind a "known failure".
//
// The addresses are now derived from the zone (scripts/zone_address_picker.py)
// and each case records the resolution it was verified against.
//
// These are file-level checks — no stack, no database. That bounds what this
// spec can prove: it validates the file's internal agreement, never
// file-vs-zoning-corpus. tests/test_eval_address_zones.py covers the latter,
// resolving each address through the production get_address_profile path
// against a live Postgres and skipping where there isn't one. The two are
// deliberately split for the same reason ABS-463/464 were: the ~180k-parcel
// HRM ingest is not present in CI or in an e2e worktree database.
//
// Specifically asserts:
//   1. Every case has an address_resolution block whose resolved_zone is the
//      case's own zone.
//   2. Every case rests on a ROOFTOP match, or says in its notes why not.
//   3. No case claims a zone the Regional Centre schedule does not map
//      (ER-1 is defined by the by-law but mapped nowhere).
//   4. Turn 1 names the case's address, and names its zone — except TC-001,
//      where misstating the zone is the test.
//   5. No two cases share an address.
//
// This spec deliberately does NOT shell out to pytest; see the note in
// abs463-bylaw-reference-validation.spec.ts for why a pytest session inside a
// Playwright worker starves the WebKit projects.

import * as fs from "fs";
import * as path from "path";
import { expect, test } from "@playwright/test";

const REPO_ROOT = path.resolve(__dirname, "../../../");
const PROMPTS_FILE = path.join(REPO_ROOT, "evals", "regional_centre_test_prompts.json");

type AddressResolution = {
  resolved_zone: string;
  resolution_quality: string;
  location_type: string | null;
  location_confidence: number | null;
  location_resolver: string | null;
  parcel_pid: string | null;
};

type TestCase = {
  id: string;
  zone: string;
  address: string;
  address_resolution: AddressResolution;
  notes: string;
  turns: { turn: number; role: string; message: string }[];
  expected_answer_keywords: string[];
};

// ABS-466's resolution-quality vocabulary. Only "rooftop" means the point was
// matched to a building.
const RESOLUTION_QUALITIES = new Set([
  "rooftop",
  "interpolated",
  "centroid",
  "approximate",
  "unknown",
]);

// The 25 zone codes the Regional Centre schedule maps (bylaw_area_id 23).
const MAPPED_ZONES = new Set([
  "CDD-1", "CDD-2", "CEN-1", "CEN-2", "CH-1", "CH-2", "CLI", "COR", "DD",
  "DH", "DND", "ER-2", "ER-3", "H", "HCD-SV", "HR-1", "HR-2", "HRI", "INS",
  "LI", "PCF", "RPK", "UC-1", "UC-2", "WA",
]);

// TC-001's turn 1 asserts ER-1 while the case is HR-1. The advisor is expected
// to correct the user; "fixing" the text would delete the test.
const ADVERSARIAL_ZONE_PREMISE = new Set(["TC-001"]);

function loadPrompts(): TestCase[] {
  expect(fs.existsSync(PROMPTS_FILE), `${PROMPTS_FILE} must exist`).toBe(true);
  return JSON.parse(fs.readFileSync(PROMPTS_FILE, "utf-8")) as TestCase[];
}

test.describe("ABS-467: eval addresses confirm their zone", () => {
  let prompts: TestCase[];

  test.beforeAll(() => {
    prompts = loadPrompts();
  });

  test("every case records the resolution it was verified against", () => {
    const failures: string[] = [];
    for (const tc of prompts) {
      const resolution = tc.address_resolution;
      if (!resolution) {
        failures.push(
          `${tc.id}: no address_resolution — run ` +
            "scripts/verify_eval_address_zones.py --repair",
        );
        continue;
      }
      if (resolution.resolved_zone !== tc.zone) {
        failures.push(
          `${tc.id}: recorded resolved_zone "${resolution.resolved_zone}" ` +
            `but the case claims "${tc.zone}"`,
        );
      }
      if (!RESOLUTION_QUALITIES.has(resolution.resolution_quality)) {
        failures.push(
          `${tc.id}: resolution_quality "${resolution.resolution_quality}" is ` +
            "not one of ABS-466's qualities",
        );
      }
    }
    expect(failures).toEqual([]);
  });

  test("every case rests on a ROOFTOP match, or declares why not", () => {
    // An estimated point can select the neighbouring parcel's zone on the next
    // data refresh. That is tolerable only when it is written down.
    const undeclared = prompts
      .filter((tc) => tc.address_resolution?.resolution_quality !== "rooftop")
      .filter((tc) => !(tc.notes ?? "").toLowerCase().includes("resolution"))
      .map((tc) => `${tc.id} (${tc.address_resolution?.resolution_quality})`);
    expect(undeclared).toEqual([]);
  });

  test("no case claims a zone the schedule does not map", () => {
    // TC-017 claimed ER-1, which the by-law defines (Part I s.30) but the
    // zoning schedule places nowhere — so no address could ever confirm it.
    const unmapped = prompts
      .filter((tc) => !MAPPED_ZONES.has(tc.zone))
      .map((tc) => `${tc.id}: ${tc.zone}`);
    expect(unmapped).toEqual([]);
  });

  test("turn 1 names the case's address", () => {
    const drifted: string[] = [];
    for (const tc of prompts) {
      const civic = tc.address.split(",")[0].trim().toLowerCase();
      if (!tc.turns[0].message.toLowerCase().includes(civic)) {
        drifted.push(`${tc.id}: turn 1 never names "${civic}"`);
      }
    }
    expect(drifted, "the address moved and the conversation did not follow").toEqual([]);
  });

  test("turn 1 and the zone field agree, except where disagreeing is the test", () => {
    const violations: string[] = [];
    for (const tc of prompts) {
      const namesZone = tc.turns[0].message.includes(tc.zone);
      if (ADVERSARIAL_ZONE_PREMISE.has(tc.id)) {
        if (namesZone) {
          violations.push(`${tc.id}: the adversarial premise was "fixed" away`);
        }
        continue;
      }
      if (!namesZone) {
        violations.push(`${tc.id}: turn 1 never names ${tc.zone}`);
      }
    }
    expect(violations).toEqual([]);
  });

  test("the zone appears in every case's expected keywords", () => {
    const missing = prompts
      .filter((tc) => !tc.expected_answer_keywords.includes(tc.zone))
      .map((tc) => `${tc.id}: ${tc.zone}`);
    expect(missing, "the grader would look for a stale zone code").toEqual([]);
  });

  test("no two cases share an address", () => {
    // TC-009 and TC-016 were both "100 Wyse Road" — an address in no zone at
    // all — so one bad geocode silently governed two cases.
    const seen = new Map<string, string[]>();
    for (const tc of prompts) {
      const key = tc.address.trim().toLowerCase();
      seen.set(key, [...(seen.get(key) ?? []), tc.id]);
    }
    const shared = [...seen.entries()]
      .filter(([, ids]) => ids.length > 1)
      .map(([address, ids]) => `${address}: ${ids.join(", ")}`);
    expect(shared).toEqual([]);
  });

  test("TC-001 keeps its adversarial premise on a re-derived address", () => {
    const tc001 = prompts.find((tc) => tc.id === "TC-001");
    expect(tc001, "TC-001 must exist").toBeDefined();
    expect(tc001!.zone).toBe("HR-1");
    expect(tc001!.expected_answer_keywords).toContain("HR-1");
    expect(tc001!.turns[0].message).toContain("ER-1");
    // ABS-463 pinned this to an interpolated address on Oxford Street; ABS-467
    // re-derived it from the zone, so the premise now rests on a ROOFTOP match.
    expect(tc001!.address_resolution.resolution_quality).toBe("rooftop");
  });
});
