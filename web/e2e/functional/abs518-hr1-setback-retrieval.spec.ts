// ABS-518: an HR-1 setback question is answered from the HR chapter
//
// TC-027 asked for the side setback, the rear setback, the streetwall height
// and the maximum height at 5261 Kent Street (HR-1). The advisor answered the
// height and the streetwall and then wrote its own heading — "What I Could NOT
// Retrieve — Side and Rear Setbacks for the Main Building" — reporting that its
// searches had returned "ER-zone setback tables (Table 9 — not applicable to
// HR-1)" instead of the sections that state the HR-1 standard.
//
// The hazard is structural and it is the one evals/regional_centre_zone_chapter_map.json
// was built to describe: **a section only governs its own chapter's zones**.
// The Regional Centre states the same rule shape once per built-form chapter
// over different numbers, and no section names its own zone —
//
//   Part V, Chapter 7: … within the HR-2 and HR-1 Zones   → s.198, s.199
//   Part V, Chapter 9: … within the ER3, ER-2, ER-1 Zones → s.229, Table 9
//
// — so on words alone an HR-1 question matches the wrong chapter just as well
// as the right one. Full write-up in docs/ABS-518-ZONE-SCOPE-EXCLUSION.md.
//
// ABS-518 changes no product surface: it is a ranking fix inside the retrieval
// library. So, following the precedent of abs486-retrieval-eval.spec.ts and
// abs502-retrieval-baseline-freshness.spec.ts, this spec reads the committed
// artifacts directly — no running server, no database, no network. The
// behavioural reproduction (an ER special-area number offered as an HR-1
// answer) lives in tests/bylaw_retrieval/test_zone_scope_exclusion.py, which
// fails without the fix.
//
// What is asserted here, and why each is worth a gate:
//
//   1. The pair TC-027 needed is labelled. RQ-D19 → Part V > 198 (side) and
//      RQ-D20 → Part V > 199 (rear), both naming HR-1. Deleting a label is the
//      easiest way for a fixed bug to silently come back.
//   2. The committed baseline records both inside the top ten. This is the
//      regression gate proper: BASELINE.json is re-recorded on every ranking
//      change, so a change that buries s.198 or s.199 has to either fail here
//      or be committed with the failure visible in the diff.
//   3. Their labels keep saying they are guards, not reproductions. Both
//      passed at k=10 before the fix (ranks 5 and 5). Stripping that caveat
//      would let a later reader quote a passing RQ-D19 as evidence the defect
//      was caught here, which is exactly the "generated expectation read as an
//      attested one" error evals/golden/README.md exists to prevent.
//   4. One chapter, both its zones. s.198 is labelled for HR-2 (RQ-D15) and
//      HR-1 (RQ-D19). A ranker that bound only the zone a chapter heading
//      happens to list first would pass one and fail the other, and a single
//      entry could not tell.
//   5. **The general invariant.** For every question that names a zone, every
//      Regional Centre section it is labelled with must live in a chapter that
//      governs that zone — or in a chapter that declares no zone at all, which
//      is silent rather than adverse. This is the ticket's principle applied to
//      the whole set rather than to its two new rows: it makes it impossible to
//      add an ER-chapter label to an HR question, which is precisely the
//      mistake the retriever was making.

import * as fs from "fs";
import * as path from "path";
import { expect, test } from "@playwright/test";

const REPO_ROOT = path.resolve(__dirname, "../../../");
const QUERIES_FILE = path.join(REPO_ROOT, "evals", "retrieval", "queries.json");
const BASELINE_FILE = path.join(REPO_ROOT, "evals", "retrieval", "BASELINE.json");
const CHAPTER_MAP_FILE = path.join(
  REPO_ROOT,
  "evals",
  "regional_centre_zone_chapter_map.json",
);

type Anchor = { bylaw?: string; citation_path?: string; text_prefix?: string };
type LabelledQuery = {
  id: string;
  category: string;
  question: string;
  acceptable: Anchor[];
  notes?: string;
};
type BaselineRow = { id: string; hit: boolean; first_hit_rank: number | null };
type Chapter = {
  part: string;
  chapter: number;
  title: string;
  zones: string[];
  sections: number[];
};

const queries: LabelledQuery[] = JSON.parse(
  fs.readFileSync(QUERIES_FILE, "utf8"),
).queries;
const baseline = JSON.parse(fs.readFileSync(BASELINE_FILE, "utf8"));
const chapterMap = JSON.parse(fs.readFileSync(CHAPTER_MAP_FILE, "utf8"));

const byId = new Map(queries.map((q) => [q.id, q]));
const baselineById = new Map<string, BaselineRow>(
  (baseline.queries as BaselineRow[]).map((row) => [row.id, row]),
);

/** The two sections TC-027 asked for, and the entries that label them. */
const HR1_SETBACK_PAIR = [
  { id: "RQ-D19", citationPath: "Part V > 198", standard: "side setback" },
  { id: "RQ-D20", citationPath: "Part V > 199", standard: "rear setback" },
];

test.describe("ABS-518 — HR-1 side and rear setbacks stay retrievable", () => {
  for (const { id, citationPath, standard } of HR1_SETBACK_PAIR) {
    test(`${id} labels the HR-1 ${standard} to ${citationPath}`, () => {
      const query = byId.get(id);
      expect(query, `${id} is missing from queries.json`).toBeTruthy();
      expect(query!.category).toBe("dimensional");
      expect(query!.question).toMatch(/\bHR-1\b/);
      expect(query!.question.toLowerCase()).toContain(standard);

      const paths = query!.acceptable
        .filter((a) => a.bylaw === "Regional Centre")
        .map((a) => a.citation_path);
      expect(paths).toContain(citationPath);
    });

    test(`${id} is retrieved inside the top ten by the committed baseline`, () => {
      const row = baselineById.get(id);
      expect(row, `${id} is missing from BASELINE.json`).toBeTruthy();
      expect(
        row!.hit,
        `${id} is not retrieved — re-record the baseline only if the miss is intended`,
      ).toBe(true);
      expect(row!.first_hit_rank).not.toBeNull();
      expect(row!.first_hit_rank!).toBeGreaterThan(0);
      expect(row!.first_hit_rank!).toBeLessThanOrEqual(baseline.k);
    });

    test(`${id} still says it is a regression guard, not a reproduction`, () => {
      // Both passed at k=10 before the fix. The note is what stops a future
      // reader treating a green RQ-D19 as proof the defect was caught here.
      const notes = (byId.get(id)!.notes ?? "").toLowerCase();
      expect(notes).toContain("regression guard");
      expect(notes).toContain("test_zone_scope_exclusion");
    });
  }

  test("one chapter governs HR-2 and HR-1, and both are labelled to s.198", () => {
    // Part V, Chapter 7 scopes both zones. RQ-D15 asks for s.198 under HR-2 and
    // RQ-D19 under HR-1; a ranker bound to only one of a chapter's zones passes
    // one and fails the other, and either entry alone would hide that.
    const chapter = (chapterMap.chapters as Chapter[]).find((c) =>
      c.sections.includes(198),
    );
    expect(chapter, "no chapter in the map contains s.198").toBeTruthy();
    expect(new Set(chapter!.zones)).toEqual(new Set(["HR-2", "HR-1"]));
    expect(chapter!.sections).toContain(199);

    for (const [id, zone] of [
      ["RQ-D15", "HR-2"],
      ["RQ-D19", "HR-1"],
    ] as const) {
      const query = byId.get(id);
      expect(query, `${id} is missing from queries.json`).toBeTruthy();
      expect(query!.question).toContain(zone);
      expect(
        query!.acceptable.map((a) => a.citation_path),
        `${id} should be labelled to the section that states the standard`,
      ).toContain("Part V > 198");
    }
  });

  test("no zone-named question is labelled to a section of another zone's chapter", () => {
    // The ticket's principle, applied to the whole set: "a section only governs
    // its own chapter's zones". A chapter declaring no zone (Chapter 1, General
    // Built Form and Siting Requirements) is silent rather than adverse, so it
    // is allowed for any zone.
    const sectionToChapter = new Map<number, Chapter>();
    for (const chapter of chapterMap.chapters as Chapter[]) {
      for (const section of chapter.sections) {
        sectionToChapter.set(section, chapter);
      }
    }

    const zoneCodes = [
      ...new Set((chapterMap.chapters as Chapter[]).flatMap((c) => c.zones)),
    ].sort((a, b) => b.length - a.length);
    // "ER3" and "ER-3" are the same zone; the by-law's own Chapter 9 heading
    // prints both spellings in one breath.
    const zonePattern = new RegExp(
      `\\b(${zoneCodes.map((c) => c.replace("-", "-?")).join("|")})\\b`,
      "gi",
    );
    const canonical = (raw: string) => raw.toUpperCase().replace("-", "");

    const violations: string[] = [];
    for (const query of queries) {
      const named = new Set(
        (query.question.match(zonePattern) ?? []).map(canonical),
      );
      if (named.size === 0) continue;

      for (const anchor of query.acceptable) {
        if (anchor.bylaw !== "Regional Centre") continue;
        const match = /^Part [IVXL]+ > (\d+)/.exec(anchor.citation_path ?? "");
        if (!match) continue;

        const chapter = sectionToChapter.get(Number(match[1]));
        if (!chapter) {
          violations.push(
            `${query.id}: ${anchor.citation_path} is in no chapter of the map`,
          );
          continue;
        }
        if (chapter.zones.length === 0) continue; // silent, not adverse
        const governs = chapter.zones.some((z) => named.has(canonical(z)));
        if (!governs) {
          violations.push(
            `${query.id} names ${[...named].join("/")} but is labelled to ` +
              `${anchor.citation_path}, which sits in "${chapter.title}" ` +
              `(governs ${chapter.zones.join(", ")})`,
          );
        }
      }
    }

    expect(violations, violations.join("\n")).toEqual([]);
  });
});
