// ABS-463: expected_bylaw_references validated against the real corpus
//
// Before this issue, no case in regional_centre_test_prompts.json had a
// validated reference set. TC-001..TC-010 carried labels that resolve but point
// at the wrong provisions (`Table 3` is minimum lot area, and was used as a
// setback table for five cases); TC-011..TC-020 carried [].
//
// These are corpus/schema checks — no running server needed. They assert
// against evals/regional_centre_bylaw_reference_index.json, the snapshot
// scripts/build_bylaw_reference_index.py writes from document_id=4. The
// snapshot exists because the 4,341-fragment Halifax ingest is not present in
// CI or in an e2e worktree database.
//
// Specifically asserts:
//   1. All 20 cases have non-empty expected_bylaw_references.
//   2. Every reference resolves to a real citation_path in the index.
//   3. The index and the eval file cover exactly the same reference set, so a
//      reference cannot be edited in without regenerating the snapshot.
//   4. The specific discredited references ABS-463 removed stay removed.
//   5. Part V zone-chapter boundaries hold (Sections 110/111/112/120 are DD
//      provisions and were being cited for CEN-1, CEN-2, DH and COR).
//   6. Table 1A/1B/1C are matched to the zones they actually cover.
//   7. TC-001's zone field matches the geocoded zone (HR-1, not ER-1).
//   8. The Python guard (tests/test_eval_bylaw_references.py) passes.

import { spawnSync } from "child_process";
import * as fs from "fs";
import * as path from "path";
import { expect, test } from "@playwright/test";

const REPO_ROOT = path.resolve(__dirname, "../../../");
const PROMPTS_FILE = path.join(REPO_ROOT, "evals", "regional_centre_test_prompts.json");
const INDEX_FILE = path.join(
  REPO_ROOT,
  "evals",
  "regional_centre_bylaw_reference_index.json",
);
const VENV_PYTHON = path.join(REPO_ROOT, ".venv", "bin", "python");

type TestCase = {
  id: string;
  zone: string;
  turns: { turn: number; role: string; message: string }[];
  expected_bylaw_references: string[];
  expected_answer_keywords: string[];
};

type IndexEntry = {
  reference: string;
  kind: string;
  resolved: boolean;
  ambiguous: boolean;
  verified_by: { sql: string; params: Record<string, unknown> };
  matches: { fragment_id: number; citation_path: string; page: number; excerpt: string }[];
};

type ReferenceIndex = {
  bylaw_name: string;
  document_id: number;
  references: Record<string, IndexEntry>;
};

// Each of these resolves as a label, which is exactly why "it resolves" was
// never a sufficient bar. Value = what the corpus actually says.
const DISCREDITED: Record<string, string> = {
  "Table 3": "minimum lot area (Established Residential Special Areas), not setbacks",
  "Table 4": "minimum lot area (Schmidtville HCD), not setbacks or lot coverage",
  "Table 5": "minimum lot frontage, not height",
  "Section 9(a)": "no citation_path in the ingest; the deck exemption is 9(1)(c)",
  "Section 9(d)": "home office uses, not a deck permit exemption",
  "Section 120(a)": "no citation_path; Section 120 is a DD streetwall rule",
  "Section 120(b)": "no citation_path; the parking rule is Section 433 + Table 15",
};

// Part V is chaptered by zone; a section only governs its own chapter.
const PART_V_CHAPTERS: { range: [number, number]; zones: string[] }[] = [
  { range: [107, 128], zones: ["DD"] },
  { range: [129, 148], zones: ["DH"] },
  { range: [156, 175], zones: ["CEN-2", "CEN-1"] },
  { range: [176, 194], zones: ["COR"] },
  { range: [195, 211], zones: ["HR-2", "HR-1"] },
  { range: [226, 235], zones: ["ER-3", "ER-2", "ER-1"] },
  { range: [253, 267], zones: ["INS"] },
  { range: [305, 312], zones: ["PCF", "RPK"] },
];

const USE_TABLE_ZONES: Record<string, string[]> = {
  "Table 1A": ["DD", "DH", "CEN-2", "CEN-1", "COR", "HR-2", "HR-1"],
  "Table 1B": ["ER-3", "ER-2", "ER-1", "CH-2", "CH-1"],
  "Table 1C": [
    "CLI", "LI", "HRI", "INS", "UC-2", "UC-1", "DND", "H", "PCF", "RPK", "WA",
  ],
};

function loadPrompts(): TestCase[] {
  expect(fs.existsSync(PROMPTS_FILE), `${PROMPTS_FILE} must exist`).toBe(true);
  return JSON.parse(fs.readFileSync(PROMPTS_FILE, "utf-8")) as TestCase[];
}

function loadIndex(): ReferenceIndex {
  expect(
    fs.existsSync(INDEX_FILE),
    `${INDEX_FILE} must exist — run scripts/build_bylaw_reference_index.py`,
  ).toBe(true);
  return JSON.parse(fs.readFileSync(INDEX_FILE, "utf-8")) as ReferenceIndex;
}

test.describe("ABS-463: bylaw reference validation", () => {
  let prompts: TestCase[];
  let index: ReferenceIndex;

  test.beforeAll(() => {
    prompts = loadPrompts();
    index = loadIndex();
  });

  test("the reference index targets the Regional Centre by-law ingest", () => {
    expect(index.bylaw_name).toBe("Regional Centre Land Use By-Law");
    expect(index.document_id).toBe(4);
    expect(Object.keys(index.references).length).toBeGreaterThan(0);
  });

  test("all 20 cases have non-empty expected_bylaw_references", () => {
    const empty = prompts
      .filter((tc) => !tc.expected_bylaw_references?.length)
      .map((tc) => tc.id);
    expect(empty, "TC-011..TC-020 were empty before ABS-463").toEqual([]);
    expect(prompts.length).toBe(20);
  });

  test("every reference resolves to a real citation_path in document_id=4", () => {
    const failures: string[] = [];
    for (const tc of prompts) {
      for (const ref of tc.expected_bylaw_references) {
        const entry = index.references[ref];
        if (!entry) {
          failures.push(
            `${tc.id}: "${ref}" is absent from the index — re-run ` +
              "scripts/build_bylaw_reference_index.py",
          );
          continue;
        }
        if (!entry.resolved || entry.matches.length === 0) {
          failures.push(`${tc.id}: "${ref}" resolves to no fragment`);
          continue;
        }
        if (entry.ambiguous) {
          failures.push(
            `${tc.id}: "${ref}" matches ${entry.matches.length} fragments`,
          );
        }
        for (const match of entry.matches) {
          if (!match.citation_path) {
            failures.push(
              `${tc.id}: "${ref}" matched fragment ${match.fragment_id} with a ` +
                "NULL citation_path — that provision is unreachable",
            );
          }
        }
      }
    }
    expect(failures).toEqual([]);
  });

  test("index and eval file cover exactly the same reference set", () => {
    const used = new Set(prompts.flatMap((tc) => tc.expected_bylaw_references));
    const indexed = new Set(Object.keys(index.references));
    const missing = [...used].filter((r) => !indexed.has(r)).sort();
    const stale = [...indexed].filter((r) => !used.has(r)).sort();
    expect(missing, "in the eval file but never verified").toEqual([]);
    expect(stale, "verified but no longer referenced").toEqual([]);
  });

  test("every index entry records the query that verified it", () => {
    const undocumented = Object.entries(index.references)
      .filter(([, entry]) => !entry.verified_by?.sql)
      .map(([ref]) => ref);
    expect(undocumented).toEqual([]);
  });

  test("no discredited reference has come back", () => {
    const violations: string[] = [];
    for (const tc of prompts) {
      for (const ref of tc.expected_bylaw_references) {
        if (ref in DISCREDITED) {
          violations.push(`${tc.id}: "${ref}" — ${DISCREDITED[ref]}`);
        }
      }
    }
    expect(violations).toEqual([]);
  });

  test("no case cites a Part V section from another zone's chapter", () => {
    const violations: string[] = [];
    for (const tc of prompts) {
      for (const ref of tc.expected_bylaw_references) {
        const match = /^Section (\d+)$/.exec(ref);
        if (!match) continue;
        const number = Number(match[1]);
        for (const chapter of PART_V_CHAPTERS) {
          const [low, high] = chapter.range;
          if (number >= low && number <= high && !chapter.zones.includes(tc.zone)) {
            violations.push(
              `${tc.id} (zone ${tc.zone}): ${ref} governs ${chapter.zones.join("/")}`,
            );
          }
        }
      }
    }
    expect(violations).toEqual([]);
  });

  test("permitted-use table matches the case's zone", () => {
    const violations: string[] = [];
    for (const tc of prompts) {
      for (const ref of tc.expected_bylaw_references) {
        const zones = USE_TABLE_ZONES[ref];
        if (zones && !zones.includes(tc.zone)) {
          violations.push(
            `${tc.id} (zone ${tc.zone}) cites ${ref}, which covers ${zones.join(", ")}`,
          );
        }
      }
    }
    expect(violations, "Table 1A/1B were swapped on nine cases").toEqual([]);
  });

  test("TC-001 zone matches the geocoded zone (HR-1, not ER-1)", () => {
    const tc001 = prompts.find((tc) => tc.id === "TC-001");
    expect(tc001, "TC-001 must exist").toBeDefined();
    // Source of truth: halifax_zoning_boundaries GlobalID
    // 5f05d7eb-5062-473c-b463-465c7443cd8d, ZONE=HR-1, on PID 00078147.
    expect(tc001!.zone).toBe("HR-1");
    expect(tc001!.expected_answer_keywords).toContain("HR-1");
    // The turn text still misstates the zone on purpose — that is the test.
    expect(tc001!.turns[0].message).toContain("ER-1");
  });

  test("the Python reference guard passes", () => {
    const result = spawnSync(
      VENV_PYTHON,
      ["-m", "pytest", "tests/test_eval_bylaw_references.py", "-q"],
      { cwd: REPO_ROOT, encoding: "utf-8" },
    );
    expect(result.status, `${result.stdout ?? ""}\n${result.stderr ?? ""}`).toBe(0);
  });
});
