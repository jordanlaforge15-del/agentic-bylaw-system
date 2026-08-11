// ABS-464: the reference index's provenance must describe a real corpus, and
// --check must not claim more than it verified.
//
// ABS-463 shipped evals/regional_centre_bylaw_reference_index.json with
// provenance fields — document_id, source_fragment_count, reference_count —
// recording which corpus the snapshot came from. `--check` compared only the
// per-reference resolutions (`_comparable()` strips ids and counts on purpose)
// and then printed "OK: 101 references resolve; snapshot is current." With the
// index at 4341 fragments and the post-ABS-461 corpus at 4337, it still exited
// 0. The message was stronger than the check.
//
// What this spec can and cannot cover
// -----------------------------------
// The real ~4,300-fragment Halifax ingest is not in the e2e database, so
// nothing here can compare the snapshot against the live corpus. That axis is
// tests/test_bylaw_reference_index_check.py, which runs `--check` for real
// wherever Postgres has the ingest and skips where it doesn't.
//
// What is left for this spec is the half that needs no database, and it is not
// nothing: every failure mode below is a hand-edit to the committed JSON — a
// count nudged to make a check pass, a reference spliced in without
// regenerating. That is exactly how ABS-464's defect became invisible.
//
// Like the ABS-463 spec beside it, this deliberately does NOT shell out to
// pytest: a pytest session blocks a Playwright worker for 13-50s and starves
// the WebKit projects (see the ABS-6 note in playwright.config.ts).

import * as fs from "fs";
import * as path from "path";
import { expect, test } from "@playwright/test";

const REPO_ROOT = path.resolve(__dirname, "../../../");
const INDEX_FILE = path.join(
  REPO_ROOT,
  "evals",
  "regional_centre_bylaw_reference_index.json",
);
const BUILDER = path.join(REPO_ROOT, "scripts", "build_bylaw_reference_index.py");

type IndexEntry = {
  matches: { fragment_id: number; citation_path: string }[];
};

type ReferenceIndex = {
  bylaw_name: string;
  document_id: number;
  source_fragment_count: number;
  reference_count: number;
  references: Record<string, IndexEntry>;
};

function loadIndex(): ReferenceIndex {
  expect(
    fs.existsSync(INDEX_FILE),
    `${INDEX_FILE} must exist — run scripts/build_bylaw_reference_index.py`,
  ).toBe(true);
  return JSON.parse(fs.readFileSync(INDEX_FILE, "utf-8")) as ReferenceIndex;
}

test.describe("ABS-464: reference index provenance", () => {
  let index: ReferenceIndex;

  test.beforeAll(() => {
    index = loadIndex();
  });

  test("the snapshot records which corpus it came from", () => {
    // Absent or null provenance is the state that makes drift undetectable —
    // there is nothing for --check to compare against.
    expect(Number.isInteger(index.document_id)).toBe(true);
    expect(Number.isInteger(index.source_fragment_count)).toBe(true);
    expect(Number.isInteger(index.reference_count)).toBe(true);
    expect(index.source_fragment_count).toBeGreaterThan(0);
    expect(index.reference_count).toBeGreaterThan(0);
  });

  test("reference_count describes the entries actually in the file", () => {
    // Catches a reference hand-spliced into (or deleted from) the index
    // without re-running the builder.
    expect(index.reference_count).toBe(Object.keys(index.references).length);
  });

  test("source_fragment_count is consistent with the fragments the index cites", () => {
    // A corpus cannot hold fewer fragments than this snapshot claims to have
    // resolved within it. Weak as a bound, but it is the strongest statement
    // available without the corpus, and it fails on a count typo'd to a small
    // number to silence a check.
    const cited = new Set<number>();
    for (const entry of Object.values(index.references)) {
      for (const match of entry.matches) {
        expect(Number.isInteger(match.fragment_id)).toBe(true);
        cited.add(match.fragment_id);
      }
    }
    expect(cited.size).toBeGreaterThan(0);
    expect(index.source_fragment_count).toBeGreaterThanOrEqual(cited.size);
  });

  test("--check does not claim the snapshot is current without verifying it", () => {
    // A source-level guard, because the assertion is about what the builder
    // *says*, and the wording is the whole defect: the old success line
    // asserted "snapshot is current" from a check that never looked at the
    // corpus size. If a future edit reinstates that claim, or drops the
    // provenance comparison, this fails.
    const source = fs.readFileSync(BUILDER, "utf-8");
    expect(
      source.includes("def provenance_drift("),
      "build_bylaw_reference_index.py must define provenance_drift()",
    ).toBe(true);
    expect(
      source.includes("provenance_drift(committed, index)"),
      "--check must call provenance_drift() against the committed snapshot",
    ).toBe(true);
    expect(
      source.includes("snapshot is current"),
      'the success message must not re-assert "snapshot is current" — say what ' +
        "was actually compared",
    ).toBe(false);
  });
});
