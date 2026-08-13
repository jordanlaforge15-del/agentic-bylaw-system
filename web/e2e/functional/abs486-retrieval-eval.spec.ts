// ABS-486: the retrieval eval's own invariants, asserted offline
//
// `evals/retrieval/queries.json` and `evals/retrieval/BASELINE.json` are the
// first Recall@k artifact in this repo. Every future scoring, fusion, chunking
// or citation-path change will be argued for or against by comparing a fresh
// run against that baseline, which makes the *artifact* load-bearing in a way
// no product surface is: if it drifts, a regression and an improvement become
// indistinguishable, and nothing in the product would fail to say so.
//
// So this spec asserts the properties a reviewer would otherwise have to take
// on trust:
//
//   1. Tier. The set carries `agent-drafted, pending human spot-check` in its
//      own provenance header, and BASELINE.json repeats it. This is the whole
//      reason the file cannot be quoted as evidence of correctness — see
//      evals/golden/README.md, which exists because a generated expectation and
//      an attested one must never be summed.
//   2. Size and coverage. At least 50 questions, spanning all six categories.
//      A count check alone cannot see a whole category quietly disappearing.
//   3. Labels are content-addressed. Every acceptable fragment is named by a
//      citation_path or an exact text_prefix, never by a bare id —
//      source_fragment.id is a sequence value a re-ingest reassigns wholesale.
//      The `fragment_ids` snapshot must carry exactly one id per anchor.
//   4. Determinism. No spatial question may name an address: an address goes
//      through the geocoder, and a baseline that moves with a network call is
//      not a baseline. Every spatial entry carries a literal geometry point.
//   5. The baseline describes the query set next to it, and its headline
//      numbers agree with its own per-query rows — the guard against a
//      hand-edited Recall@10.
//
// ABS-486 changes no product behaviour: it adds a query set, an offline
// harness and a measurement. There is nothing to drive in a browser, so this
// spec reads the committed artifacts directly — no running server, no
// database, no network. The Python-side twin is
// tests/scripts/test_eval_retrieval_recall.py, which additionally exercises the
// metric arithmetic through a stubbed retrieval service.
//
// This spec deliberately does NOT shell out to pytest; see the note in
// abs463-bylaw-reference-validation.spec.ts for why a pytest session inside a
// Playwright worker starves the WebKit projects.

import * as fs from "fs";
import * as path from "path";
import { expect, test } from "@playwright/test";

const REPO_ROOT = path.resolve(__dirname, "../../../");
const QUERIES_FILE = path.join(REPO_ROOT, "evals", "retrieval", "queries.json");
const BASELINE_FILE = path.join(REPO_ROOT, "evals", "retrieval", "BASELINE.json");
const README_FILE = path.join(REPO_ROOT, "evals", "retrieval", "README.md");

const TIER = "agent-drafted, pending human spot-check";
const MIN_QUERIES = 50;
const REQUIRED_CATEGORIES = [
  "dimensional",
  "permitted_use",
  "definition",
  "zone_anchored",
  "spatial",
  "citation_lookup",
];

type Anchor = {
  bylaw?: string;
  citation_path?: string;
  text_prefix?: string;
};

type LabelledQuery = {
  id: string;
  category: string;
  question: string;
  acceptable: Anchor[];
  fragment_ids: number[];
  location?: { geometry?: { type?: string; coordinates?: number[] } };
};

type BaselineRow = {
  id: string;
  hit: boolean;
  first_hit_rank: number | null;
  reciprocal_rank: number;
  acceptable_fragment_ids: number[];
  ranked_fragment_ids: number[];
};

const QUERY_SET = JSON.parse(fs.readFileSync(QUERIES_FILE, "utf8")) as {
  provenance: Record<string, string>;
  queries: LabelledQuery[];
};
const BASELINE = JSON.parse(fs.readFileSync(BASELINE_FILE, "utf8")) as {
  k: number;
  query_count: number;
  recall_at_k: number;
  mrr: number;
  query_set_provenance: Record<string, string>;
  corpus: { retrieval_enabled_documents: unknown[] };
  queries: BaselineRow[];
};

test.describe("ABS-486 retrieval eval artifacts", () => {
  test("the query set declares its tier and is not confused with the golden subset", () => {
    expect(QUERY_SET.provenance.tier).toBe(TIER);
    expect(QUERY_SET.provenance.authored_by).toBeTruthy();
    expect(QUERY_SET.provenance.review_status).toBe("unreviewed");
    // The header must say, in the file itself, that this is not the attested
    // tier — a reader who only ever opens queries.json has to learn it there.
    expect(QUERY_SET.provenance.what_this_is_not).toMatch(/golden/i);
    expect(BASELINE.query_set_provenance.tier).toBe(TIER);
    expect(fs.readFileSync(README_FILE, "utf8")).toContain(TIER);
  });

  test("at least 50 questions spanning every required category", () => {
    expect(QUERY_SET.queries.length).toBeGreaterThanOrEqual(MIN_QUERIES);
    const categories = new Set(QUERY_SET.queries.map((q) => q.category));
    for (const category of REQUIRED_CATEGORIES) {
      expect(
        QUERY_SET.queries.filter((q) => q.category === category).length,
        `category ${category} has no queries`,
      ).toBeGreaterThan(0);
    }
    // No category outside the declared vocabulary — a typo'd category would
    // otherwise pass the coverage check above while grading nothing.
    expect([...categories].sort()).toEqual([...REQUIRED_CATEGORIES].sort());
  });

  test("query ids are unique and every question carries a label", () => {
    const ids = QUERY_SET.queries.map((q) => q.id);
    expect(new Set(ids).size).toBe(ids.length);
    for (const query of QUERY_SET.queries) {
      expect(query.question.trim().length, `${query.id} has no question`).toBeGreaterThan(0);
      expect(query.acceptable.length, `${query.id} has no acceptable fragment`).toBeGreaterThan(0);
    }
  });

  test("labels are content-addressed anchors, snapshotted one id per anchor", () => {
    for (const query of QUERY_SET.queries) {
      for (const anchor of query.acceptable) {
        expect(anchor.bylaw, `${query.id} anchor has no bylaw`).toBeTruthy();
        const kinds = [anchor.citation_path, anchor.text_prefix].filter(Boolean);
        expect(kinds.length, `${query.id} anchor must have exactly one locator`).toBe(1);
      }
      // The snapshot is what a stale-corpus check compares against, so it has
      // to line up with the anchors one-for-one.
      expect(query.fragment_ids.length, `${query.id} snapshot is not one id per anchor`).toBe(
        query.acceptable.length,
      );
      for (const fragmentId of query.fragment_ids) {
        expect(Number.isInteger(fragmentId)).toBe(true);
      }
    }
  });

  test("spatial questions carry a literal point, never an address", () => {
    const spatial = QUERY_SET.queries.filter((q) => q.category === "spatial");
    expect(spatial.length).toBeGreaterThan(0);
    for (const query of spatial) {
      const geometry = query.location?.geometry;
      expect(geometry, `${query.id} has no location.geometry`).toBeTruthy();
      expect(geometry!.type).toBe("Point");
      expect(geometry!.coordinates?.length).toBe(2);
      // An address anywhere on a spatial entry would send the harness through
      // the geocoder and make the baseline network-dependent.
      const slot = query.location as Record<string, unknown>;
      for (const addressField of ["address", "civic_number", "street", "parcel_id"]) {
        expect(slot[addressField], `${query.id} names ${addressField}`).toBeUndefined();
      }
    }
  });

  test("non-spatial questions carry no location slot at all", () => {
    for (const query of QUERY_SET.queries.filter((q) => q.category !== "spatial")) {
      expect(query.location, `${query.id} carries a location slot`).toBeUndefined();
    }
  });

  test("the baseline describes the query set committed beside it", () => {
    expect(BASELINE.k).toBe(10);
    expect(BASELINE.query_count).toBe(QUERY_SET.queries.length);
    expect(BASELINE.queries.map((r) => r.id)).toEqual(QUERY_SET.queries.map((q) => q.id));
    // Without the corpus fingerprint a Recall@k number cannot be compared to
    // anything: the same harness over a re-ingest is a different measurement.
    expect(BASELINE.corpus.retrieval_enabled_documents.length).toBeGreaterThan(0);
    for (const row of BASELINE.queries) {
      const query = QUERY_SET.queries.find((q) => q.id === row.id)!;
      expect(row.acceptable_fragment_ids).toEqual(query.fragment_ids);
    }
  });

  test("the headline numbers agree with the baseline's own per-query rows", () => {
    const rows = BASELINE.queries;
    const recall = rows.filter((r) => r.hit).length / rows.length;
    const mrr = rows.reduce((sum, r) => sum + r.reciprocal_rank, 0) / rows.length;
    expect(BASELINE.recall_at_k).toBeCloseTo(recall, 4);
    expect(BASELINE.mrr).toBeCloseTo(mrr, 4);

    for (const row of rows) {
      expect(row.ranked_fragment_ids.length).toBeLessThanOrEqual(BASELINE.k);
      if (row.hit) {
        expect(row.first_hit_rank).not.toBeNull();
        const hitId = row.ranked_fragment_ids[row.first_hit_rank! - 1];
        expect(row.acceptable_fragment_ids).toContain(hitId);
        expect(row.reciprocal_rank).toBeCloseTo(1 / row.first_hit_rank!, 4);
      } else {
        expect(row.first_hit_rank).toBeNull();
        expect(row.reciprocal_rank).toBe(0);
        for (const fragmentId of row.ranked_fragment_ids) {
          expect(row.acceptable_fragment_ids).not.toContain(fragmentId);
        }
      }
    }
  });
});
