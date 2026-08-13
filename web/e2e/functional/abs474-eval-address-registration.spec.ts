// ABS-474: an eval address must be one the municipality registers on its parcel
//
// ABS-471's guards asked "does this address resolve to the zone the case
// claims?" and "did the geocoder give us a rooftop match?". Five cases passed
// both while naming a property that does not exist:
//
//   TC-004  "251 Stairs Street"      — HRM registers 249/251/257 Windmill Road
//   TC-011  "1273 Edward Street"     — HRM registers 6016 University Avenue
//   TC-014  "3123 Kempt Road"        — HRM registers 3111..3129 Kempt / Robie / Young
//   TC-016  "15 Kings Wharf Place"   — HRM registers 12 Cutwater / 16 Kings Wharf Place
//   TC-019  "1462 Birchdale Avenue"  — HRM registers 1462 Thornvale Avenue
//
// The addresses were composed by reverse-geocoding a parcel's interior point,
// which returns the *nearest* street address to a point rather than the address
// assigned to the parcel: on a corner lot that is the civic number from one
// street and the route from another, and on a multi-frontage parcel it is a
// number interpolated between two real ones. Neither earlier check could see
// it, and the zone round-trip never can — a string composed from a parcel
// geocodes straight back onto that parcel, so the zone confirms and the
// confidence reads ROOFTOP.
//
// Asserts here:
//   1. Every case records a registered_civics snapshot — the civic addresses
//      HRM assigns to the parcel the case was derived from.
//   2. Every case's address is one of them.
//   3. The rule bites: each of the five real fabrications above is rejected by
//      the comparison running in this spec, against its own parcel's list.
//   4. Comparison is on punctuation- and case-insensitive text, so formatting
//      noise never masquerades as a defect — but a different number, street or
//      community always does, because that difference is the whole signal.
//
// Corpus/schema checks — no running server, no database, and no network. The
// snapshot is what makes that possible: the register itself is not ingested
// (its prerequisites live in the production resolver — ABS-475), so the
// municipality's answer is captured at authoring time by
// `scripts/verify_eval_address_zones.py --backfill-civics` and re-asserted
// here. ABS-475 replaces the snapshot with a live lookup.
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
  parcel_pid?: string | null;
  registered_civics?: string[];
};

type TestCase = {
  id: string;
  address: string;
  address_resolution?: AddressResolution;
};

const CASES: TestCase[] = JSON.parse(fs.readFileSync(PROMPTS_FILE, "utf8"));

/** Mirrors scripts/zone_address_picker._normalize_for_match. */
function normalize(address: string): string {
  return address.replace(/,/g, " ").toLowerCase().split(/\s+/).filter(Boolean).join(" ");
}

function isRegistered(address: string, registered: string[]): boolean {
  const target = normalize(address);
  return registered.some((known) => normalize(known) === target);
}

test.describe("ABS-474 eval addresses are registered on their parcel", () => {
  test("the corpus loads and every case carries a parcel and its registered civics", () => {
    expect(CASES.length).toBeGreaterThan(0);
    for (const testCase of CASES) {
      const resolution = testCase.address_resolution;
      expect(
        resolution,
        `${testCase.id} has no address_resolution block`,
      ).toBeTruthy();
      expect(
        resolution?.parcel_pid,
        `${testCase.id} records no parcel_pid — the register cannot be asked ` +
          `about a parcel the case does not name`,
      ).toBeTruthy();
      expect(
        resolution?.registered_civics?.length,
        `${testCase.id} records no registered_civics — re-run ` +
          `scripts/verify_eval_address_zones.py --backfill-civics`,
      ).toBeGreaterThan(0);
    }
  });

  for (const testCase of CASES) {
    test(`${testCase.id}: its address is a civic HRM puts on parcel ${
      testCase.address_resolution?.parcel_pid ?? "(none)"
    }`, () => {
      const registered = testCase.address_resolution?.registered_civics ?? [];
      expect(
        isRegistered(testCase.address, registered),
        `${testCase.id}: "${testCase.address}" is not registered on parcel ` +
          `${testCase.address_resolution?.parcel_pid}. HRM registers: ` +
          `${registered.join(", ")}. The zone may still be correct — that is ` +
          `the trap — but the address names a property that does not exist.`,
      ).toBe(true);
    });
  }

  // The five real fabrications, each against the parcel list that exposed it.
  // Pinned so the comparison cannot be loosened without a test naming the
  // defect it would let back in.
  const FABRICATIONS: Array<[string, string, string[]]> = [
    [
      "TC-004 corner lot: civic number from Windmill, route from Stairs",
      "251 Stairs Street, Dartmouth, NS",
      [
        "249 Windmill Road, Dartmouth, NS",
        "251 Windmill Road, Dartmouth, NS",
        "257 Windmill Road, Dartmouth, NS",
      ],
    ],
    [
      "TC-011 address real elsewhere, not on this parcel",
      "1273 Edward Street, Halifax, NS",
      ["6016 University Avenue, Halifax, NS"],
    ],
    [
      "TC-014 multi-frontage parcel: number interpolated between 3121 and 3125",
      "3123 Kempt Road, Halifax, NS",
      [
        "3121 Kempt Road, Halifax, NS",
        "3125 Kempt Road, Halifax, NS",
        "3128 Robie Street, Halifax, NS",
      ],
    ],
    [
      "TC-016 neighbouring civic on a shared parcel",
      "15 Kings Wharf Place, Dartmouth, NS",
      ["12 Cutwater, Dartmouth, NS", "16 Kings Wharf Place, Dartmouth, NS"],
    ],
    [
      "TC-019 right number, adjacent street",
      "1462 Birchdale Avenue, Halifax, NS",
      ["1462 Thornvale Avenue, Halifax, NS"],
    ],
  ];

  for (const [label, fabricated, registered] of FABRICATIONS) {
    test(`rejects ${label}`, () => {
      expect(
        isRegistered(fabricated, registered),
        `"${fabricated}" must not be accepted against ${registered.join(", ")}`,
      ).toBe(false);
    });
  }

  test("formatting noise is not a defect, but a real difference always is", () => {
    const registered = ["1801 Hollis Street, Halifax, NS"];
    expect(isRegistered("1801  Hollis Street,Halifax, NS", registered)).toBe(true);
    expect(isRegistered("1801 HOLLIS STREET, HALIFAX, NS", registered)).toBe(true);
    expect(isRegistered("1803 Hollis Street, Halifax, NS", registered)).toBe(false);
    expect(isRegistered("1801 Hollis Street, Dartmouth, NS", registered)).toBe(false);
  });
});
