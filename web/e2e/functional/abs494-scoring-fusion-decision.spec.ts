// ABS-494: the scoring/fusion experiment matrix and its decision, asserted offline
//
// This issue's deliverable is not a product surface. It is a *decision* —
// whether to replace the hand-tuned scorer's max()+bonus fusion with RRF or an
// FTS hybrid — plus the evidence that decision rests on. So what has to be
// guarded here is the evidence, and specifically the one way this evidence has
// already been wrong once.
//
// The failure this spec exists to prevent
// ---------------------------------------
// `scripts/check_retrieval_baseline.py` (ABS-502) names it outright:
//
//     "ABS-494 argued its case ('+0.34, zero regressions') against a control
//      that had already moved."
//
// The first run of this matrix measured a `current` arm scoring Recall@10 =
// 0.1618 and concluded that RRF and FTS-hybrid arms beat it by +0.34. Between
// that run and the decision, ABS-492 (provision-in-context scoring) and
// ABS-500 (the table channel) landed, and the shipped retriever moved to
// 0.5588 on the same query set — past every arm the matrix had recommended.
// The measurement was not wrong; it had simply stopped describing the program
// it claimed to describe, and nothing in the repo would have said so.
//
// A results table whose control has drifted from the live baseline is worse
// than no table: it does not merely fail to detect the drift, it actively
// certifies a superseded winner. Assertion 2 below makes that structurally
// impossible rather than a thing a reviewer has to remember.
//
// What is asserted
// ----------------
//   1. Both artifacts the Definition of Done names exist: a results table with
//      a per-query-class breakdown, and a decision doc in docs/decisions/.
//   2. The control arm's headline numbers equal BASELINE.json's, to the digit.
//      This is the load-bearing one — see above.
//   3. Every arm named in RESULTS.md has a machine-readable arms/*.json beside
//      it, and vice versa. A table row with no data file cannot be re-derived.
//   4. Each arm's reported recall_at_k is what its own per-query rows say it
//      is. The guard against a hand-edited headline.
//   5. Every arm graded the same query set at the same k over the same corpus.
//      Arms measured against different inputs are not comparable, and a table
//      that puts them in adjacent rows is claiming they are.
//   6. The decision doc states a verdict and cites the control it was decided
//      against, so a future reader can tell whether it has gone stale the same
//      way its predecessor did.
//
// ABS-494 changes no browser-visible behaviour, so — like
// abs486-retrieval-eval.spec.ts, whose structure this follows — the spec reads
// the committed artifacts directly: no running server, no database, no
// network. It deliberately does NOT shell out to pytest; see the note in
// abs463-bylaw-reference-validation.spec.ts for why a pytest session inside a
// Playwright worker starves the WebKit projects.

import * as fs from "fs";
import * as path from "path";
import { expect, test } from "@playwright/test";

const REPO_ROOT = path.resolve(__dirname, "../../../");
const EXPERIMENTS_DIR = path.join(REPO_ROOT, "evals", "retrieval", "experiments");
const RESULTS_FILE = path.join(EXPERIMENTS_DIR, "RESULTS.md");
const ARMS_DIR = path.join(EXPERIMENTS_DIR, "arms");
const BASELINE_FILE = path.join(REPO_ROOT, "evals", "retrieval", "BASELINE.json");
const DECISIONS_DIR = path.join(REPO_ROOT, "docs", "decisions");
const DECISION_FILE = path.join(DECISIONS_DIR, "ABS-494-SCORING-FUSION-DECISION.md");

const CONTROL_ARM = "current";
const REQUIRED_CATEGORIES = [
  "dimensional",
  "permitted_use",
  "definition",
  "zone_anchored",
  "spatial",
  "citation_lookup",
];

type ArmReport = {
  arm: string;
  summary: string;
  k: number;
  query_count: number;
  recall_at_k: number;
  set_recall_at_k: number;
  mrr: number;
  by_category: Record<string, { query_count: number; recall_at_k: number }>;
  queries: {
    id: string;
    category: string;
    acceptable_fragment_ids: number[];
    ranked_fragment_ids: number[];
    hit: boolean;
    first_hit_rank: number | null;
  }[];
};

function readJson<T>(file: string): T {
  return JSON.parse(fs.readFileSync(file, "utf8")) as T;
}

function armFiles(): string[] {
  return fs
    .readdirSync(ARMS_DIR)
    .filter((name) => name.endsWith(".json"))
    .sort();
}

function loadArms(): ArmReport[] {
  return armFiles().map((name) => readJson<ArmReport>(path.join(ARMS_DIR, name)));
}

/** Arm names appearing in a RESULTS.md table's leading `\`name\`` column. */
function armNamesInResults(results: string): Set<string> {
  const names = new Set<string>();
  for (const line of results.split("\n")) {
    const match = /^\|\s*`([a-z0-9_]+)`\s*\|/.exec(line.trim());
    if (match) names.add(match[1]);
  }
  return names;
}

test.describe("ABS-494 scoring & fusion decision artifacts", () => {
  test("the Definition of Done's two artifacts exist", () => {
    expect(
      fs.existsSync(RESULTS_FILE),
      "evals/retrieval/experiments/RESULTS.md must be committed",
    ).toBe(true);
    expect(
      fs.existsSync(DECISION_FILE),
      "a decision doc must be committed under docs/decisions/ — the issue " +
        "requires one whether the outcome is ship or keep",
    ).toBe(true);
    expect(armFiles().length, "at least a control and one alternative").toBeGreaterThan(1);
  });

  // The one that matters. See the header.
  test("the control arm reproduces the live BASELINE.json, to the digit", () => {
    const baseline = readJson<{
      k: number;
      query_count: number;
      recall_at_k: number;
      set_recall_at_k: number;
      mrr: number;
      by_category: Record<string, { recall_at_k: number }>;
    }>(BASELINE_FILE);
    const control = loadArms().find((arm) => arm.arm === CONTROL_ARM);
    expect(control, `an arm named '${CONTROL_ARM}' must exist`).toBeTruthy();

    expect(
      control!.recall_at_k,
      "the matrix's control has drifted from the shipped retriever's baseline. " +
        "Every delta in RESULTS.md is quoted against this number, so a drifted " +
        "control certifies a winner that may already be beaten by dev. " +
        "Re-run scripts/eval_retrieval_experiment.py — do NOT edit this by hand.",
    ).toBeCloseTo(baseline.recall_at_k, 4);
    expect(control!.set_recall_at_k).toBeCloseTo(baseline.set_recall_at_k, 4);
    expect(control!.mrr).toBeCloseTo(baseline.mrr, 4);
    expect(control!.k).toBe(baseline.k);
    expect(control!.query_count).toBe(baseline.query_count);

    // Per category too: a control can match on the headline while disagreeing
    // underneath, and the per-class table is what the ship gate is read from.
    for (const category of REQUIRED_CATEGORIES) {
      expect(
        control!.by_category[category]?.recall_at_k,
        `control's ${category} recall must match the baseline's`,
      ).toBeCloseTo(baseline.by_category[category].recall_at_k, 4);
    }
  });

  test("every arm in the table has a data file, and every data file a row", () => {
    const results = fs.readFileSync(RESULTS_FILE, "utf8");
    const tabled = armNamesInResults(results);
    const onDisk = new Set(loadArms().map((arm) => arm.arm));

    for (const name of onDisk) {
      expect(tabled.has(name), `arms/${name}.json has no row in RESULTS.md`).toBe(true);
    }
    for (const name of tabled) {
      expect(onDisk.has(name), `RESULTS.md row '${name}' has no arms/${name}.json`).toBe(true);
    }
    expect(tabled.has(CONTROL_ARM)).toBe(true);
  });

  test("each arm's headline recall is what its own per-query rows say", () => {
    for (const arm of loadArms()) {
      expect(arm.queries.length, `${arm.arm}: query_count agrees`).toBe(arm.query_count);

      const hits = arm.queries.filter((q) => q.hit).length;
      expect(
        arm.recall_at_k,
        `${arm.arm}: headline recall disagrees with its per-query hits`,
      ).toBeCloseTo(hits / arm.queries.length, 4);

      for (const query of arm.queries) {
        // `hit` must mean what the metric says it means: an acceptable
        // fragment inside the top k. A row that says hit=true while its
        // ranking contains none would inflate the arm silently.
        const overlap = query.ranked_fragment_ids
          .slice(0, arm.k)
          .some((id) => query.acceptable_fragment_ids.includes(id));
        expect(overlap, `${arm.arm}/${query.id}: hit flag disagrees with ranking`).toBe(
          query.hit,
        );
        expect(
          query.ranked_fragment_ids.length,
          `${arm.arm}/${query.id}: ranking longer than k`,
        ).toBeLessThanOrEqual(arm.k);
        if (query.hit) {
          expect(query.first_hit_rank, `${arm.arm}/${query.id}`).not.toBeNull();
          expect(query.first_hit_rank!).toBeGreaterThan(0);
          expect(query.first_hit_rank!).toBeLessThanOrEqual(arm.k);
        } else {
          expect(query.first_hit_rank, `${arm.arm}/${query.id}`).toBeNull();
        }
      }
    }
  });

  test("all arms graded the same questions at the same k", () => {
    const arms = loadArms();
    const control = arms.find((arm) => arm.arm === CONTROL_ARM)!;
    const questionIds = control.queries.map((q) => q.id).join(",");

    for (const arm of arms) {
      expect(arm.k, `${arm.arm}: k must match the control's`).toBe(control.k);
      expect(
        arm.queries.map((q) => q.id).join(","),
        `${arm.arm}: graded a different question set than the control — ` +
          "arms measured on different inputs are not comparable",
      ).toBe(questionIds);
      // Labels are resolved per run; two arms disagreeing about what is
      // acceptable for a question means the corpus moved mid-matrix.
      for (const [index, query] of arm.queries.entries()) {
        expect(
          query.acceptable_fragment_ids.join(","),
          `${arm.arm}/${query.id}: acceptable set differs from the control's`,
        ).toBe(control.queries[index].acceptable_fragment_ids.join(","));
      }
    }

    // Categories must all still be represented, or a per-class ship gate is
    // being read off a set that quietly lost a class.
    for (const category of REQUIRED_CATEGORIES) {
      expect(
        control.by_category[category]?.query_count,
        `category '${category}' missing from the graded set`,
      ).toBeGreaterThan(0);
    }
  });

  test("the decision doc states a verdict and the control it was decided against", () => {
    const decision = fs.readFileSync(DECISION_FILE, "utf8");
    const baseline = readJson<{ recall_at_k: number }>(BASELINE_FILE);

    expect(
      /^#\s+ABS-494/m.test(decision),
      "decision doc should be titled for the issue it closes",
    ).toBe(true);
    expect(
      /\b(KEEP|SHIP)\b/.test(decision),
      "the doc must state an unambiguous verdict — KEEP or SHIP",
    ).toBe(true);
    // The control's Recall@10 must appear in the text. This is what lets a
    // future reader notice the doc has gone stale the same way the first
    // matrix did, instead of trusting a conclusion measured against a
    // retriever that no longer exists.
    expect(
      decision.includes(baseline.recall_at_k.toFixed(4)),
      `the doc must quote the control it was decided against ` +
        `(${baseline.recall_at_k.toFixed(4)}), so its staleness is detectable`,
    ).toBe(true);
  });
});
