// ABS-471: eval-corpus integrity — zone-appropriate citations + geocode floor
//
// An audit found defects in 17 of the 20 cases in
// evals/regional_centre_test_prompts.json: 7 wrong zones, 5 addresses that do
// not exist, one zone with no polygons in the dataset at all, and a dozen
// keyword/reference errors. None of it was caught and none of it *could* have
// been. scripts/build_bylaw_reference_index.py --check proves each
// expected_bylaw_references entry resolves to a real fragment — a real guard,
// and it works — but it answers only "does this citation exist?", never "is it
// correct for the zone this case declares?". `zone`, `address` and
// `expected_answer_keywords` had no validation of any kind, and every one of
// the 17 defects lived in exactly those fields.
//
// Specifically asserts:
//   1. The derived chapter map (evals/regional_centre_zone_chapter_map.json) is
//      present, describes the Regional Centre by-law, and carries the
//      zone-specific chapters and all four permitted-use tables.
//   2. G2 — every Section/Table token in expected_answer_keywords falls in a
//      chapter that governs the case's zone.
//   3. G3 — the same rule over expected_bylaw_references, so a case whose zone
//      changes cannot keep the old zone's references.
//   4. The rule bites: the six real regressions ABS-470 removed (Section 196 /
//      200 on INS, DD, COR and CDD-2 cases, Section 111 on a DH case, Table 1A
//      on an RPK case, Section 344 — Schmidtville HCD — on an ER-3 case) are
//      each rejected by the implementation running here.
//   5. G1's offline half — every case's recorded geocode clears the confidence
//      floor. TC-002/003/004 used to sit at 0.60 and were accepted.
//
// These are corpus/schema checks — no running server, no database. That bounds
// what the spec can prove: it never intersects a point against a zoning
// polygon. tests/test_eval_address_spatial.py does that against a live
// Postgres and skips where there isn't one, because the ~180k-parcel Halifax
// ingest is not present in CI or in an e2e worktree database. The chapter map
// is the bridge: derived from the corpus by scripts/build_zone_chapter_map.py,
// committed, and re-checked against the ingest by tests/test_zone_chapter_map.py.
//
// Not checked anywhere: numeric keywords ("6.0 m", "80%"). See
// docs/ABS-471-EVAL-CORPUS-GUARDS.md.
//
// This spec deliberately does NOT shell out to pytest; see the note in
// abs463-bylaw-reference-validation.spec.ts for why a pytest session inside a
// Playwright worker starves the WebKit projects.

import * as fs from "fs";
import * as path from "path";
import { expect, test } from "@playwright/test";

const REPO_ROOT = path.resolve(__dirname, "../../../");
const PROMPTS_FILE = path.join(REPO_ROOT, "evals", "regional_centre_test_prompts.json");
const CHAPTER_MAP_FILE = path.join(
  REPO_ROOT,
  "evals",
  "regional_centre_zone_chapter_map.json",
);

// Mirrors scripts/verify_eval_corpus_integrity.MIN_GEOCODE_CONFIDENCE. 0.95 is
// a rooftop match on the building, 0.85 an interpolation along the street, 0.60
// the centre of a block — a point that picks its zoning polygon by luck.
const MIN_GEOCODE_CONFIDENCE = 0.85;

type AddressResolution = {
  resolved_zone: string;
  resolution_quality: string;
  location_confidence: number | null;
};

type TestCase = {
  id: string;
  zone: string;
  address: string;
  address_resolution?: AddressResolution;
  expected_bylaw_references: string[];
  expected_answer_keywords: string[];
};

type Chapter = {
  part: string;
  chapter: number;
  title: string;
  first_section: number;
  last_section: number;
  zones: string[];
  sections: number[];
};

type ChapterMap = {
  bylaw_name: string;
  document_id: number;
  chapters: Chapter[];
  permitted_use_tables: Record<string, string[]>;
};

type Citation = { kind: "section" | "table"; text: string; label: string; number?: number };

// The reference grammar of scripts/build_bylaw_reference_index.py, matched
// inside a string rather than against the whole of it: keywords are usually
// bare tokens ("Section 254") but nothing enforces that, and a rule that only
// matched whole strings would silently skip the rest.
const SECTION_TOKEN = /\bSection (\d+[A-Z]?)((?:\(\w+\))*)/g;
const TABLE_TOKEN = /\bTable (\d+[A-Z]?)\b/g;

function citationsIn(values: string[]): Citation[] {
  const found: Citation[] = [];
  const seen = new Set<string>();
  for (const value of values ?? []) {
    if (typeof value !== "string") continue;
    for (const match of value.matchAll(SECTION_TOKEN)) {
      const text = `Section ${match[1]}${match[2]}`;
      if (seen.has(text)) continue;
      seen.add(text);
      found.push({
        kind: "section",
        text,
        label: `Section ${match[1]}`,
        number: Number.parseInt(match[1], 10),
      });
    }
    for (const match of value.matchAll(TABLE_TOKEN)) {
      const text = `Table ${match[1]}`;
      if (seen.has(text)) continue;
      seen.add(text);
      found.push({ kind: "table", text, label: text });
    }
  }
  return found;
}

// Why the citation cannot belong to a case in `zone`, or null if it can.
// Mirrors scripts/eval_zone_chapters.zone_violation.
function zoneViolation(zone: string, citation: Citation, map: ChapterMap): string | null {
  if (citation.kind === "table") {
    const covered = map.permitted_use_tables[citation.label];
    if (!covered || covered.includes(zone)) return null;
    const correct =
      Object.entries(map.permitted_use_tables)
        .sort(([a], [b]) => a.localeCompare(b))
        .find(([, zones]) => zones.includes(zone))?.[0] ?? "not published";
    return (
      `${citation.text} covers ${covered.join(", ")} — not ${zone}. ` +
      `The permitted-use table for ${zone} is ${correct}.`
    );
  }
  if (citation.number === undefined || Number.isNaN(citation.number)) return null;
  // A number the corpus places in more than one chapter cannot be attributed;
  // the ingest labels one Part XVI fragment "7", colliding with Part I's
  // Section 7. Staying silent there is deliberate.
  const chapters = map.chapters.filter((c) => c.sections.includes(citation.number!));
  if (chapters.length !== 1) return null;
  const chapter = chapters[0];
  if (chapter.zones.length === 0 || chapter.zones.includes(zone)) return null;
  return (
    `${citation.text} is in Part ${chapter.part}, Chapter ${chapter.chapter} ` +
    `(sections ${chapter.first_section}-${chapter.last_section}), which governs ` +
    `${chapter.zones.join(", ")} — not ${zone}`
  );
}

function violationsFor(cases: TestCase[], field: keyof TestCase, map: ChapterMap): string[] {
  const lines: string[] = [];
  for (const tc of cases) {
    for (const citation of citationsIn((tc[field] as string[]) ?? [])) {
      const reason = zoneViolation(tc.zone, citation, map);
      if (reason) lines.push(`${tc.id} (${String(field)}, zone ${tc.zone}): ${reason}`);
    }
  }
  return lines;
}

function loadPrompts(): TestCase[] {
  expect(fs.existsSync(PROMPTS_FILE), `${PROMPTS_FILE} must exist`).toBe(true);
  return JSON.parse(fs.readFileSync(PROMPTS_FILE, "utf-8")) as TestCase[];
}

function loadChapterMap(): ChapterMap {
  expect(
    fs.existsSync(CHAPTER_MAP_FILE),
    `${CHAPTER_MAP_FILE} must exist — run scripts/build_zone_chapter_map.py`,
  ).toBe(true);
  return JSON.parse(fs.readFileSync(CHAPTER_MAP_FILE, "utf-8")) as ChapterMap;
}

test.describe("ABS-471: eval corpus integrity guards", () => {
  let prompts: TestCase[];
  let chapterMap: ChapterMap;

  test.beforeAll(() => {
    prompts = loadPrompts();
    chapterMap = loadChapterMap();
  });

  test("the chapter map describes the Regional Centre by-law ingest", () => {
    expect(chapterMap.bylaw_name).toBe("Regional Centre Land Use By-Law");
    expect(chapterMap.document_id).toBe(4);
    const zoned = chapterMap.chapters.filter((c) => c.zones.length > 0);
    expect(zoned.length, "the by-law has 18+ zone-specific chapters").toBeGreaterThanOrEqual(
      18,
    );
    expect(Object.keys(chapterMap.permitted_use_tables).sort()).toEqual([
      "Table 1A",
      "Table 1B",
      "Table 1C",
      "Table 1D",
    ]);
    // The boundaries the whole rule rests on, spot-checked so a truncated or
    // hand-edited map cannot make every assertion below vacuous.
    const hr = chapterMap.chapters.find((c) => c.part === "V" && c.chapter === 7);
    expect(hr?.zones).toEqual(["HR-2", "HR-1"]);
    expect([hr?.first_section, hr?.last_section]).toEqual([195, 211]);
  });

  test("G2: expected_answer_keywords cite only the case's own zone chapters", () => {
    expect(violationsFor(prompts, "expected_answer_keywords", chapterMap)).toEqual([]);
  });

  test("G3: expected_bylaw_references cite only the case's own zone chapters", () => {
    expect(violationsFor(prompts, "expected_bylaw_references", chapterMap)).toEqual([]);
  });

  test("the rule rejects every citation defect ABS-470 removed", () => {
    // Real cases from the audit, not hypotheticals. Without this the two tests
    // above would pass just as happily on a rule that never rejects anything.
    const regressions: { zone: string; token: string; governs: string }[] = [
      { zone: "INS", token: "Section 196", governs: "HR-2, HR-1" },
      { zone: "DD", token: "Section 200", governs: "HR-2, HR-1" },
      { zone: "COR", token: "Section 200", governs: "HR-2, HR-1" },
      { zone: "CDD-2", token: "Section 196", governs: "HR-2, HR-1" },
      { zone: "DH", token: "Section 111", governs: "DD" },
      { zone: "ER-3", token: "Section 344", governs: "HCD-SV" },
    ];
    for (const { zone, token, governs } of regressions) {
      const reason = zoneViolation(zone, citationsIn([token])[0], chapterMap);
      expect(reason, `${token} on a ${zone} case must be rejected`).not.toBeNull();
      expect(reason).toContain(governs);
      expect(reason).toContain(zone);
    }

    const tableReason = zoneViolation("RPK", citationsIn(["Table 1A"])[0], chapterMap);
    expect(tableReason, "Table 1A on an RPK case must be rejected").not.toBeNull();
    expect(tableReason, "name the table the zone IS in").toContain("Table 1C");
  });

  test("the rule accepts a provision from the case's own chapter", () => {
    // The other half of "it bites": a guard that rejects everything is no
    // guard either, and would be deleted the first time it blocked a fix.
    const allowed: { zone: string; token: string }[] = [
      { zone: "HR-2", token: "Section 200" },
      { zone: "DD", token: "Section 111" },
      { zone: "INS", token: "Section 254" },
      { zone: "RPK", token: "Table 1C" },
      { zone: "ER-3", token: "Table 1B" },
      // General provisions — Part I development permits, Part XIII parking,
      // Part V Chapter 19 accessory structures — govern every zone.
      { zone: "ER-3", token: "Section 9" },
      { zone: "COR", token: "Section 433" },
      { zone: "ER-3", token: "Section 331" },
    ];
    for (const { zone, token } of allowed) {
      expect(
        zoneViolation(zone, citationsIn([token])[0], chapterMap),
        `${token} is valid in ${zone}`,
      ).toBeNull();
    }
  });

  test("a citation embedded in a longer keyword is still checked", () => {
    const found = citationsIn(["the streetwall rule in Section 200 applies"]);
    expect(found.map((c) => c.label)).toEqual(["Section 200"]);
    expect(zoneViolation("INS", found[0], chapterMap)).not.toBeNull();
  });

  test("G1 offline: every case's recorded geocode clears the confidence floor", () => {
    const failures: string[] = [];
    for (const tc of prompts) {
      const confidence = tc.address_resolution?.location_confidence;
      if (confidence === undefined || confidence === null) {
        failures.push(`${tc.id}: no location_confidence recorded for "${tc.address}"`);
        continue;
      }
      if (confidence < MIN_GEOCODE_CONFIDENCE) {
        failures.push(
          `${tc.id}: "${tc.address}" resolved at ${confidence} ` +
            `(${tc.address_resolution?.resolution_quality}), below the ` +
            `${MIN_GEOCODE_CONFIDENCE} floor — an estimated point selects its ` +
            "zoning polygon by luck",
        );
      }
    }
    expect(failures).toEqual([]);
  });

  test("G2 has something to check on most of the corpus", () => {
    // Nine cases keep their keywords purely descriptive ("rear setback",
    // "3.0 m"), so G2 sees eleven. The floor is here so a rewrite that strips
    // citations out of the keyword sets cannot quietly reduce it to a no-op.
    const withCitations = prompts.filter(
      (tc) => citationsIn(tc.expected_answer_keywords).length > 0,
    );
    expect(withCitations.length).toBeGreaterThanOrEqual(11);
    expect(prompts.length).toBe(20);
  });
});
