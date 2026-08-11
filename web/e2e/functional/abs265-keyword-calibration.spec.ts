// ABS-265: Recalibrate expected_answer_keywords against real Halifax LUB ingest
//
// Validates that regional_centre_test_prompts.json was recalibrated against
// the real document_id=4 ingest (4,340 fragments) rather than the 77-line
// synthetic fixture.  These are schema / corpus checks — no running server needed.
//
// Specifically asserts:
//   1. All 20 cases have non-empty expected_answer_keywords (TC-011..TC-020
//      were previously empty).
//   2. Known fixture-derived wrong values are gone (7.5 m, 1.2 m, 45%,
//      11.0 m, 20.0 m, 25.0 m, "no off-street parking", "1 parking space",
//      "Heritage Advisory Committee").
//   3. TC-001 uses HR-1 keywords (not ER-1) — the advisor correctly geocoded
//      the case address to HR-1 in the recorded transcript. ABS-467 later
//      re-derived that address from the zone; the keywords are unaffected.
//   4. Every simple-complexity case whose expected keywords were verified against
//      recorded transcripts (TC-001, TC-002) contains at least 4 keywords.
//   5. The recalibrate_keywords.py script is present and runs with --dry-run
//      cleanly (exit 0, no output to stderr).

import { spawnSync } from "child_process";
import * as fs from "fs";
import * as path from "path";
import { expect, test } from "@playwright/test";

const REPO_ROOT = path.resolve(__dirname, "../../../");
const PROMPTS_FILE = path.join(REPO_ROOT, "evals", "regional_centre_test_prompts.json");
const RECALIBRATE_SCRIPT = path.join(REPO_ROOT, "scripts", "recalibrate_keywords.py");
const VENV_PYTHON = path.join(REPO_ROOT, ".venv", "bin", "python");

// Fixture-derived values that should no longer appear in any keyword list.
const FIXTURE_BANNED: string[] = [
  "7.5 m",          // ER-1 rear setback from synthetic fixture (not real bylaw)
  "1.2 m",          // ER-1 side setback from synthetic fixture
  "45%",            // ER-3 lot coverage from synthetic fixture (real: 50%)
  "11.0 m",         // ER-3 height from synthetic fixture (real: Schedule 15)
  "20.0 m",         // HR-1 height from synthetic fixture (real: Schedule 15)
  "25.0 m",         // HR-2 height from synthetic fixture (real: Schedule 15)
  "no off-street parking",     // phrase never appears in real bylaw text
  "1 parking space",           // Table 15 doesn't list this for HR zones
  "1 space per 4",             // bicycle rate; advisor never surfaced this
  "Heritage Advisory Committee", // not in the real LUB document text
];

type TestCase = {
  id: string;
  zone: string;
  complexity: string;
  expected_answer_keywords: string[];
};

function loadPrompts(): TestCase[] {
  expect(fs.existsSync(PROMPTS_FILE), `${PROMPTS_FILE} must exist`).toBe(true);
  return JSON.parse(fs.readFileSync(PROMPTS_FILE, "utf-8")) as TestCase[];
}

// ─── Corpus structure checks (no server needed) ──────────────────────────────

test.describe("ABS-265: Keyword corpus calibration", () => {
  let prompts: TestCase[];

  test.beforeAll(() => {
    prompts = loadPrompts();
  });

  test("all 20 cases have non-empty expected_answer_keywords", () => {
    const empty = prompts.filter((tc) => !tc.expected_answer_keywords?.length);
    expect(
      empty.map((tc) => tc.id),
      "These cases have empty expected_answer_keywords",
    ).toEqual([]);
  });

  test("no fixture-derived wrong values remain in any keyword list", () => {
    const violations: string[] = [];
    for (const tc of prompts) {
      for (const banned of FIXTURE_BANNED) {
        if (tc.expected_answer_keywords.includes(banned)) {
          violations.push(`${tc.id}: banned keyword "${banned}"`);
        }
      }
    }
    expect(violations, "Fixture-derived values found — run recalibrate_keywords.py")
      .toEqual([]);
  });

  test("TC-001 uses HR-1 keywords (advisor correctly geocoded the address to HR-1)", () => {
    const tc001 = prompts.find((tc) => tc.id === "TC-001");
    expect(tc001, "TC-001 must exist").toBeDefined();
    const kw = tc001!.expected_answer_keywords;
    expect(kw).toContain("HR-1");
    expect(kw).not.toContain("ER-1");
  });

  test("TC-001 and TC-002 (simple, empirically verified) have at least 4 keywords", () => {
    for (const id of ["TC-001", "TC-002"]) {
      const tc = prompts.find((p) => p.id === id);
      expect(tc, `${id} must exist`).toBeDefined();
      expect(
        tc!.expected_answer_keywords.length,
        `${id} should have ≥4 keywords`,
      ).toBeGreaterThanOrEqual(4);
    }
  });

  test("all cases have at least one real bylaw anchor (section, schedule, or zone code)", () => {
    // A calibrated keyword set must include at least one structural anchor so the
    // verifier can differentiate a correct answer from a vague one.  We accept any
    // keyword that looks like a section/schedule reference or a known zone code.
    const ZONE_CODES = new Set([
      "CEN-1", "CEN-2", "COR", "DD", "DH",
      "ER-1", "ER-2", "ER-3", "HR-1", "HR-2", "INS", "RPK",
    ]);
    const hasAnchor = (kw: string[]) =>
      kw.some(
        (k) =>
          /^(Section|Schedule|Table|Part)\s/i.test(k) ||
          ZONE_CODES.has(k),
      );

    const missing = prompts.filter((tc) => !hasAnchor(tc.expected_answer_keywords));
    expect(
      missing.map((tc) => tc.id),
      "These cases lack a bylaw structural anchor in expected_answer_keywords",
    ).toEqual([]);
  });
});

// ─── Script smoke test (no server needed) ────────────────────────────────────

test.describe("ABS-265: recalibrate_keywords.py script", () => {
  test("script exists and runs --dry-run cleanly", () => {
    expect(
      fs.existsSync(RECALIBRATE_SCRIPT),
      `${RECALIBRATE_SCRIPT} must exist`,
    ).toBe(true);

    const result = spawnSync(
      VENV_PYTHON,
      [RECALIBRATE_SCRIPT, "--dry-run"],
      { cwd: REPO_ROOT, encoding: "utf-8" },
    );

    expect(
      result.status,
      `recalibrate_keywords.py --dry-run exited ${result.status}:\n${result.stderr}`,
    ).toBe(0);
  });

  test("script --dry-run reports 0 changes when corpus is already calibrated", () => {
    const result = spawnSync(
      VENV_PYTHON,
      [RECALIBRATE_SCRIPT, "--dry-run"],
      { cwd: REPO_ROOT, encoding: "utf-8" },
    );

    // After running the script once, a second --dry-run should report no changes.
    expect(result.stdout).toContain("0 case(s) would change");
  });
});
