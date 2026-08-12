// ABS-470: the eval's keyword layer is graded against the by-law, not against
// a neighbouring chapter.
//
// ABS-463 put a guard on `expected_bylaw_references` and ABS-467 put one on
// `zone`/`address`. Nothing has ever looked at `expected_answer_keywords`,
// which is the field the grader actually scores answers with — so every
// defect ABS-470 corrected lived there, unseen:
//
//   D1  Three cases expected a lot-coverage percentage while citing the very
//       section that denies one. ss.121 (DD), 142 (DH), 168 (CEN), 187 (COR),
//       204 (HR) and 221 (CLI) all read "No maximum required lot coverage
//       applies." The grader rewarded an invented cap and marked the correct
//       answer wrong.
//   D2  Seven cases expected a `Section NNN` from a chapter that does not
//       govern their zone — Section 196/200 (HR) sitting in INS, CEN-2, DD
//       and COR cases; Section 111 (DD) in a DH case; Table 1A in an RPK
//       case; Sections 344/63 in an ER-3 backyard-suite case.
//   D3  Both HR setback sections branch on what the lot abuts. TC-001 and
//       TC-013 expected 6.0 m — s.199(1)(a), the abutting-ER branch — at
//       addresses that abut no ER, CH, PCF or RPK lot.
//   D4  TC-002 asked about, and graded on, a "secondary suite". That is not a
//       use in this by-law; the string does not occur once in the ingest.
//
// These are file-level checks — no stack, no database — for the reason
// abs463-bylaw-reference-validation.spec.ts records: the ~180k-parcel HRM
// ingest is not present in CI or in an e2e worktree database, and shelling out
// to pytest from a Playwright worker starves the WebKit projects.

import * as fs from "fs";
import * as path from "path";
import { expect, test } from "@playwright/test";

const REPO_ROOT = path.resolve(__dirname, "../../../");
const PROMPTS_FILE = path.join(REPO_ROOT, "evals", "regional_centre_test_prompts.json");
const TAG_DOC_FILE = path.join(REPO_ROOT, "docs", "TEST_PROMPT_GENERATION.md");

type TestCase = {
  id: string;
  title: string;
  zone: string;
  tags: string[];
  turns: { turn: number; role: string; message: string }[];
  expected_bylaw_references: string[];
  expected_answer_keywords: string[];
  expected_topics: string[];
  notes: string;
};

// Part V is chaptered by zone; a section governs its own chapter and nothing
// else. Sections outside every range below (Part I-IV general provisions,
// Part VI overlays, the accessory-structure chapter) apply across zones and
// are deliberately unconstrained here.
const PART_V_CHAPTERS: { range: [number, number]; zones: string[] }[] = [
  { range: [107, 128], zones: ["DD"] },
  { range: [129, 148], zones: ["DH"] },
  { range: [156, 175], zones: ["CEN-2", "CEN-1"] },
  { range: [176, 194], zones: ["COR"] },
  { range: [195, 211], zones: ["HR-2", "HR-1"] },
  { range: [212, 225], zones: ["CLI"] },
  { range: [226, 235], zones: ["ER-3", "ER-2", "ER-1"] },
  { range: [236, 245], zones: ["CH-2", "CH-1"] },
  { range: [246, 252], zones: ["LI", "HRI"] },
  { range: [253, 267], zones: ["INS"] },
  { range: [298, 304], zones: ["DND", "H"] },
  { range: [305, 312], zones: ["PCF", "RPK"] },
  { range: [318, 325], zones: ["CDD-2", "CDD-1"] },
];

const USE_TABLE_ZONES: Record<string, string[]> = {
  "Table 1A": ["DD", "DH", "CEN-2", "CEN-1", "COR", "HR-2", "HR-1"],
  "Table 1B": ["ER-3", "ER-2", "ER-1", "CH-2", "CH-1"],
  "Table 1C": [
    "CLI", "LI", "HRI", "INS", "UC-2", "UC-1", "DND", "H", "PCF", "RPK", "WA",
  ],
  "Table 1D": ["HCD-SV"],
};

// The chapters whose lot-coverage section reads, in full, "No maximum required
// lot coverage applies." A percentage expectation in one of these zones is
// wrong no matter what number it names.
const NO_LOT_COVERAGE_CAP: Record<string, string> = {
  DD: "Section 121",
  DH: "Section 142",
  "CEN-2": "Section 168",
  "CEN-1": "Section 168",
  COR: "Section 187",
  "HR-2": "Section 204",
  "HR-1": "Section 204",
  CLI: "Section 221",
};

// The only numbered lot-coverage caps in the by-law.
const LOT_COVERAGE_CAPS: Record<string, string> = {
  "ER-3": "Section 231",
  "ER-2": "Section 231",
  "ER-1": "Section 231",
  "CH-2": "Section 243",
  "CH-1": "Section 243",
  LI: "Section 251 (80%)",
  HRI: "Section 251 (80%)",
  INS: "Section 262 (60%)",
};

// s.199(1)(a) — the rear setback where the lot abuts an ER, CH, PCF or RPK
// lot. Neither HR case in the corpus does, so 6.0 m is the wrong branch for
// both; a case that genuinely abuts one has to say so in its notes.
const ABUTTING_BRANCH_SETBACK = "6.0 m";
const HR_ZONES = new Set(["HR-1", "HR-2"]);

// TC-017 is an adversarial non-conforming-use case whose turn 1 is the
// homeowner's own (mistaken) framing. Nothing is graded on it, and its notes
// record why the phrase survives there and nowhere else.
const SECONDARY_SUITE_TURN_EXCEPTION = new Set(["TC-017"]);

const SECTION_KEYWORD = /^Section (\d+)$/;
const PERCENTAGE_KEYWORD = /^\d+(\.\d+)?%$/;

function loadPrompts(): TestCase[] {
  expect(fs.existsSync(PROMPTS_FILE), `${PROMPTS_FILE} must exist`).toBe(true);
  return JSON.parse(fs.readFileSync(PROMPTS_FILE, "utf-8")) as TestCase[];
}

test.describe("ABS-470: eval keywords are graded against the governing chapter", () => {
  let prompts: TestCase[];

  test.beforeAll(() => {
    prompts = loadPrompts();
    expect(prompts.length).toBe(20);
  });

  // ─── D2 — wrong-chapter citations ──────────────────────────────────────────

  test("every Section keyword falls in the chapter governing its case's zone", () => {
    const violations: string[] = [];
    for (const tc of prompts) {
      for (const keyword of tc.expected_answer_keywords) {
        const match = SECTION_KEYWORD.exec(keyword);
        if (!match) continue;
        const section = Number(match[1]);
        const chapter = PART_V_CHAPTERS.find(
          (c) => section >= c.range[0] && section <= c.range[1],
        );
        // Outside Part V's zone chapters: a general provision, unconstrained.
        if (!chapter) continue;
        if (!chapter.zones.includes(tc.zone)) {
          violations.push(
            `${tc.id} (${tc.zone}): "${keyword}" is in the ` +
              `${chapter.zones.join("/")} chapter (${chapter.range[0]}-${chapter.range[1]})`,
          );
        }
      }
    }
    expect(
      violations,
      "the grader would reward citing a section that does not govern the case's zone",
    ).toEqual([]);
  });

  test("every Table 1x keyword covers its case's zone", () => {
    const violations: string[] = [];
    for (const tc of prompts) {
      for (const keyword of tc.expected_answer_keywords) {
        const zones = USE_TABLE_ZONES[keyword];
        if (!zones) continue;
        if (!zones.includes(tc.zone)) {
          violations.push(`${tc.id}: ${tc.zone} is not in ${keyword}`);
        }
      }
    }
    // TC-012 expected Table 1A for an RPK case, whose turn 1 deliberately
    // misframes the question that way — grading on it rewarded repeating the
    // user's framing instead of correcting it.
    expect(violations).toEqual([]);
  });

  test("the four rechaptered cases cite their own chapter's height and streetwall sections", () => {
    const expected: Record<string, string[]> = {
      "TC-011": ["Section 254"], // INS, was Section 196 (HR)
      "TC-014": ["Section 157", "Section 164"], // CEN-2, was 196/200 (HR)
      "TC-015": ["Section 132"], // DH, was Section 111 (DD)
      "TC-016": ["Section 109", "Section 117"], // DD, was 196/200 (HR)
      "TC-018": ["Section 177", "Section 183"], // COR, was 196/200 (HR)
      "TC-012": ["Table 1C"], // RPK, was Table 1A
      "TC-019": ["Section 56", "Section 331"], // ER-3 backyard suite, was 63/344
    };
    for (const [id, keywords] of Object.entries(expected)) {
      const tc = prompts.find((p) => p.id === id);
      expect(tc, `${id} must exist`).toBeDefined();
      for (const keyword of keywords) {
        expect(tc!.expected_answer_keywords, `${id} must expect ${keyword}`).toContain(keyword);
      }
    }
  });

  test("no case expects the HR height/streetwall sections outside an HR zone", () => {
    // Section 196 and Section 200 were the single most-copied defect: four
    // cases carried them into INS, CEN-2, DD and COR.
    const strays = prompts
      .filter((tc) => !HR_ZONES.has(tc.zone))
      .filter((tc) =>
        tc.expected_answer_keywords.some((k) => k === "Section 196" || k === "Section 200"),
      )
      .map((tc) => `${tc.id} (${tc.zone})`);
    expect(strays).toEqual([]);
  });

  // ─── D1 — lot coverage ─────────────────────────────────────────────────────

  test("no case expects a lot-coverage percentage in a chapter that sets no cap", () => {
    const violations: string[] = [];
    for (const tc of prompts) {
      const denyingSection = NO_LOT_COVERAGE_CAP[tc.zone];
      if (!denyingSection) continue;
      for (const keyword of tc.expected_answer_keywords) {
        if (PERCENTAGE_KEYWORD.test(keyword)) {
          violations.push(
            `${tc.id} (${tc.zone}): expects "${keyword}" while ${denyingSection} ` +
              'reads "No maximum required lot coverage applies."',
          );
        }
      }
    }
    expect(violations).toEqual([]);
  });

  test("the surviving percentage keywords sit in zones the by-law actually caps", () => {
    const orphans: string[] = [];
    for (const tc of prompts) {
      const percentages = tc.expected_answer_keywords.filter((k) =>
        PERCENTAGE_KEYWORD.test(k),
      );
      if (percentages.length === 0) continue;
      if (!LOT_COVERAGE_CAPS[tc.zone]) {
        orphans.push(`${tc.id} (${tc.zone}): ${percentages.join(", ")}`);
      }
    }
    expect(orphans).toEqual([]);
  });

  test("70% appears nowhere — it is not a lot-coverage figure in this by-law", () => {
    // 80% is the LI/HRI figure (s.251) and 60% the INS one (s.262). 70% was
    // invented outright for TC-010.
    const hits = prompts
      .filter((tc) => tc.expected_answer_keywords.includes("70%"))
      .map((tc) => tc.id);
    expect(hits).toEqual([]);
  });

  test("80% appears only where s.251 governs", () => {
    const hits = prompts
      .filter((tc) => tc.expected_answer_keywords.includes("80%"))
      .filter((tc) => tc.zone !== "LI" && tc.zone !== "HRI")
      .map((tc) => `${tc.id} (${tc.zone})`);
    expect(hits).toEqual([]);
  });

  // ─── D3 — setback branch ───────────────────────────────────────────────────

  test("the two HR setback cases expect the branch their address actually falls in", () => {
    for (const id of ["TC-001", "TC-013"]) {
      const tc = prompts.find((p) => p.id === id);
      expect(tc, `${id} must exist`).toBeDefined();
      const keywords = tc!.expected_answer_keywords;
      expect(keywords, `${id}: s.199(1)(b) rear setback`).toContain("3.0 m");
      expect(keywords, `${id}: s.198(1)(f) side setback`).toContain("2.5 m");
      expect(
        keywords,
        `${id}: 6.0 m is s.199(1)(a), which needs an abutting ER/CH/PCF/RPK lot`,
      ).not.toContain(ABUTTING_BRANCH_SETBACK);
    }
  });

  test("an HR case may only expect 6.0 m if its notes record the abutting lot", () => {
    const undeclared = prompts
      .filter((tc) => HR_ZONES.has(tc.zone))
      .filter((tc) => tc.expected_answer_keywords.includes(ABUTTING_BRANCH_SETBACK))
      .filter((tc) => !/abuts an? (ER|CH|PCF|RPK)/i.test(tc.notes ?? ""))
      .map((tc) => tc.id);
    expect(
      undeclared,
      "s.199(1)(a) is a conditional branch, not the default rear setback",
    ).toEqual([]);
  });

  // ─── D4 — by-law terminology ───────────────────────────────────────────────

  test("no case is titled, tagged, or graded on a use the by-law does not have", () => {
    const violations: string[] = [];
    for (const tc of prompts) {
      const graded = [
        tc.title,
        ...tc.tags,
        ...tc.expected_answer_keywords,
        ...tc.expected_topics,
      ];
      for (const value of graded) {
        if (/secondary[ _]suite/i.test(value)) {
          violations.push(`${tc.id}: "${value}"`);
        }
      }
    }
    expect(
      violations,
      '"secondary suite" does not occur once in the ingest; lookup_citation ' +
        "answers unknown_use for it",
    ).toEqual([]);
  });

  test("only the adversarial case still says it in a turn, and says why", () => {
    const offenders: string[] = [];
    for (const tc of prompts) {
      const inTurns = tc.turns.some((t) => /secondary[ _]suite/i.test(t.message));
      if (!inTurns) continue;
      if (!SECONDARY_SUITE_TURN_EXCEPTION.has(tc.id)) {
        offenders.push(`${tc.id}: turn text asks about a use that does not exist`);
        continue;
      }
      if (!/secondary suite/i.test(tc.notes ?? "")) {
        offenders.push(`${tc.id}: the exception is undocumented in notes`);
      }
    }
    expect(offenders).toEqual([]);
  });

  test("TC-002 asks about, and grades on, the use Table 1B names", () => {
    const tc002 = prompts.find((p) => p.id === "TC-002");
    expect(tc002, "TC-002 must exist").toBeDefined();
    expect(tc002!.zone).toBe("ER-2");
    expect(tc002!.expected_answer_keywords).toContain("two-unit dwelling");
    expect(tc002!.expected_answer_keywords).toContain("Table 1B");
    expect(tc002!.turns[0].message).toMatch(/two-unit dwelling/i);
    expect(tc002!.tags).not.toContain("secondary_suite");
  });

  test("the documented tag vocabulary no longer offers the retired tag", () => {
    // The generator doc is where the next case's tags come from; leaving
    // `secondary_suite` listed there reintroduces it on the next case written.
    const doc = fs.readFileSync(TAG_DOC_FILE, "utf-8");
    const vocabulary = doc
      .split("\n")
      .find((line) => line.startsWith("`renovation`"));
    expect(vocabulary, "the established-tags line must exist").toBeDefined();
    expect(vocabulary!).not.toContain("`secondary_suite`");
    expect(vocabulary!).toContain("`two_unit_dwelling`");
    expect(vocabulary!).toContain("`backyard_suite`");
    expect(doc, "the retirement has to say why").toMatch(/Retired: `secondary_suite`/);
  });
});
