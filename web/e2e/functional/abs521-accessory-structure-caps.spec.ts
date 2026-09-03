// ABS-521: both caps on an accessory structure reach the reader
//
// TC-024 asked how large a garage-to-suite conversion could be at 1107 Lucknow
// Street (ER-2). Two limits apply to a new accessory structure and they bind
// together:
//
//   s.333(1)(a)  60.0 m² FOOTPRINT   in DD, DH, CEN-2, CEN-1, COR, HR-2, HR-1,
//   s.333(1.5)   93.0 m² FLOOR AREA  ER-3, ER-2, ER-1, CH-2, CH-1 — the same zones
//
// The advisor answered "must not exceed 93.0 m² of floor area" and never
// mentioned 60. That is a wrong answer, not a thin one: an owner who designs to
// 93 and ignores 60 fails the footprint cap.
//
// Why the clause was unreachable, and why no ranking change fixes it: every
// topical word a footprint question uses — accessory, structure, footprint,
// maximum — lives in s.333(1)'s stem, which ends on a colon. Strip the zone list
// and 333(1)(a) is a number. It scores 9 against its own section's 21 on the
// query it answers, and it is *also* parented to the heading printed above s.333
// rather than to s.333 — one of 1,906 clauses in the dev corpus attached that
// way. Full write-up in docs/ABS-521-PROVISION-COMPLETION.md.
//
// ABS-521 changes no product surface: it makes a retrieval payload complete. So,
// following abs486-retrieval-eval.spec.ts, abs502-retrieval-baseline-freshness
// .spec.ts and abs518-hr1-setback-retrieval.spec.ts, this spec reads the
// committed artifacts directly — no running server, no database, no network. The
// behavioural reproduction (the 60.0 figure never reaching the caller) lives in
// tests/bylaw_retrieval/test_operative_clauses.py, which fails without the fix.
//
// What is asserted here, and why each is worth a gate:
//
//   1. Both caps are labelled, as a pair, to the provision that states them.
//      Deleting a label is the easiest way for a fixed bug to come back quietly.
//   2. RQ-D21 is recorded as a MISS and its label says the miss is deliberate.
//      This is the inverse of the usual gate and it is the important one: the
//      clause carrying 60.0 is unreachable by rank, the harness grades ranking,
//      and an entry that quietly flipped to "hit" would most likely mean
//      somebody had redefined what the harness counts as retrieved — which would
//      raise every historical number at once and break comparability with every
//      baseline since ABS-486.
//   3. RQ-D22 is recorded as a hit. The reachable half must stay reachable.
//   4. Neither label may claim to demonstrate the fix, and both must name the
//      unit test that does. Same discipline as ABS-518's RQ-D19/RQ-D20 caveat:
//      a generated expectation read as an attested one is the error
//      evals/golden/README.md exists to prevent.
//   5. **The ranking did not move.** ABS-521 is a payload change, and that claim
//      is only worth anything if it is checked. Every question that predates
//      RQ-D21/RQ-D22 must have exactly the hit and rank the ABS-518 baseline
//      recorded for it. A future scoring change that arrives disguised as a
//      completion change fails here.

import * as fs from "fs";
import * as path from "path";
import { execFileSync } from "child_process";
import { expect, test } from "@playwright/test";

const REPO_ROOT = path.resolve(__dirname, "../../../");
const QUERIES_FILE = path.join(REPO_ROOT, "evals", "retrieval", "queries.json");
const BASELINE_FILE = path.join(REPO_ROOT, "evals", "retrieval", "BASELINE.json");
const DOC_FILE = path.join(REPO_ROOT, "docs", "ABS-521-PROVISION-COMPLETION.md");

type Anchor = { bylaw?: string; citation_path?: string; text_prefix?: string };
type LabelledQuery = {
  id: string;
  category: string;
  question: string;
  acceptable: Anchor[];
  notes?: string;
};
type BaselineRow = {
  id: string;
  hit: boolean;
  first_hit_rank: number | null;
};

const queries: LabelledQuery[] = JSON.parse(
  fs.readFileSync(QUERIES_FILE, "utf8"),
).queries;
const baseline = JSON.parse(fs.readFileSync(BASELINE_FILE, "utf8"));

const byId = new Map(queries.map((q) => [q.id, q]));
const baselineById = new Map<string, BaselineRow>(
  (baseline.queries as BaselineRow[]).map((row) => [row.id, row]),
);

/** The provision, and the two limits of it that bind together. */
const SECTION = "Part V > 333";
const FOOTPRINT_CLAUSE = "Part V > 333 > (a)";
const FLOOR_AREA_CLAUSE = "Part V > 333 > (1.5)";

test.describe("ABS-521 — both accessory-structure caps stay reachable", () => {
  test("RQ-D21 labels the footprint cap to s.333 and its clause (a)", () => {
    const query = byId.get("RQ-D21");
    expect(query, "RQ-D21 is missing from queries.json").toBeTruthy();
    expect(query!.category).toBe("dimensional");
    expect(query!.question).toMatch(/\bER-2\b/);
    expect(query!.question.toLowerCase()).toContain("footprint");

    const paths = query!.acceptable
      .filter((a) => a.bylaw === "Regional Centre")
      .map((a) => a.citation_path);
    expect(paths).toContain(SECTION);
    expect(paths).toContain(FOOTPRINT_CLAUSE);
    // The floor-area clause answers a different question. Listing it here would
    // turn RQ-D21 into a hit for free and hide the very thing it records.
    expect(paths).not.toContain(FLOOR_AREA_CLAUSE);
  });

  test("RQ-D22 labels the floor-area cap to s.333(1.5)", () => {
    const query = byId.get("RQ-D22");
    expect(query, "RQ-D22 is missing from queries.json").toBeTruthy();
    expect(query!.category).toBe("dimensional");
    expect(query!.question).toMatch(/\bER-2\b/);
    expect(query!.question.toLowerCase()).toContain("floor area");

    const paths = query!.acceptable
      .filter((a) => a.bylaw === "Regional Centre")
      .map((a) => a.citation_path);
    expect(paths).toContain(FLOOR_AREA_CLAUSE);
  });

  test("RQ-D21 is still a recorded miss, and the record is the point", () => {
    // Inverted on purpose. 333(1)(a) has no topic words of its own, so it is
    // unreachable by rank; the harness grades rank. If this flips to a hit
    // without the ranking demonstrably improving, the likeliest cause is that
    // somebody widened what counts as "retrieved" — which would silently raise
    // every number recorded since ABS-486.
    const row = baselineById.get("RQ-D21");
    expect(row, "RQ-D21 is missing from BASELINE.json").toBeTruthy();
    expect(
      row!.hit,
      "RQ-D21 now hits. If the ranking genuinely reaches Part V > 333 > (a), " +
        "that is real progress — update this expectation and say so in the " +
        "README. If instead the harness's definition of 'retrieved' changed, " +
        "revert it: every baseline since ABS-486 is measured the old way.",
    ).toBe(false);
    expect(row!.first_hit_rank).toBeNull();
  });

  test("RQ-D22 is retrieved inside the top ten by the committed baseline", () => {
    const row = baselineById.get("RQ-D22");
    expect(row, "RQ-D22 is missing from BASELINE.json").toBeTruthy();
    expect(
      row!.hit,
      "the reachable half of s.333 stopped being reachable",
    ).toBe(true);
    expect(row!.first_hit_rank).not.toBeNull();
    expect(row!.first_hit_rank!).toBeGreaterThan(0);
    expect(row!.first_hit_rank!).toBeLessThanOrEqual(baseline.k);
  });

  test("neither label claims to demonstrate the fix", () => {
    // The fix is a payload guarantee; this harness grades ranking. A future
    // reader must not be able to quote a green RQ-D22 as proof the 60.0 figure
    // is delivered — it proves only that the 93.0 clause still ranks.
    for (const id of ["RQ-D21", "RQ-D22"]) {
      const notes = (byId.get(id)?.notes ?? "").toLowerCase();
      expect(notes, `${id} has no notes`).not.toBe("");
      expect(
        notes,
        `${id} no longer names the test that actually pins the guarantee`,
      ).toContain("test_operative_clauses");
    }
    expect((byId.get("RQ-D21")!.notes ?? "").toLowerCase()).toContain(
      "recorded miss",
    );
    expect((byId.get("RQ-D22")!.notes ?? "").toLowerCase()).toContain(
      "regression guard",
    );
  });

  test("the ranking did not move: every pre-ABS-521 question is unchanged", () => {
    // ABS-521 claims to change no ranking at all. The claim is checkable, so it
    // is checked: re-derive the last 70-question baseline from git and compare
    // row by row. A scoring change arriving disguised as a completion change
    // fails here even if the headline numbers happen to land in the same place.
    //
    // The revision is found by walking this file's own history rather than by
    // counting commits back from HEAD. A HEAD~n reach is correct for exactly as
    // long as nothing else lands, and a test that quietly starts comparing the
    // wrong revision is worse than no test.
    const revisions = execFileSync(
      "git",
      ["log", "--format=%H", "--", "evals/retrieval/BASELINE.json"],
      { cwd: REPO_ROOT, encoding: "utf8", maxBuffer: 32 * 1024 * 1024 },
    )
      .split("\n")
      .filter(Boolean);

    let before: { query_count: number; queries: BaselineRow[] } | null = null;
    for (const revision of revisions) {
      const parsed = JSON.parse(
        execFileSync(
          "git",
          ["show", `${revision}:evals/retrieval/BASELINE.json`],
          { cwd: REPO_ROOT, encoding: "utf8", maxBuffer: 32 * 1024 * 1024 },
        ),
      );
      if (parsed.query_count === 70) {
        before = parsed;
        break;
      }
    }

    // A shallow clone can legitimately lack the history. Say so out loud rather
    // than passing on an empty comparison.
    test.skip(
      before === null,
      "no 70-question baseline in this checkout's history — nothing to compare",
    );

    const beforeById = new Map<string, BaselineRow>(
      before!.queries.map((row) => [row.id, row]),
    );

    const drifted: string[] = [];
    for (const [id, was] of beforeById) {
      const now = baselineById.get(id);
      if (!now) {
        drifted.push(`${id}: dropped from the set`);
        continue;
      }
      if (was.hit !== now.hit || was.first_hit_rank !== now.first_hit_rank) {
        drifted.push(
          `${id}: hit ${was.hit}@${was.first_hit_rank} -> ` +
            `${now.hit}@${now.first_hit_rank}`,
        );
      }
    }
    expect(
      drifted,
      "ABS-521 is documented as changing no ranking. These questions moved:\n" +
        drifted.join("\n"),
    ).toEqual([]);
  });

  test("the write-up states the blast radius and where it was measured", () => {
    // The ticket asks "is this s.333, or every (a) clause?". The answer is a
    // number from a script, not a sentence, and the doc has to keep saying so —
    // an unsourced 1,906 is a claim a later reader cannot check.
    const doc = fs.readFileSync(DOC_FILE, "utf8");
    expect(doc).toContain("scripts/audit_provision_parentage.py");
    expect(doc).toContain("1906");
    expect(doc).toMatch(/60\.0/);
    expect(doc).toMatch(/93\.0/);
  });
});
