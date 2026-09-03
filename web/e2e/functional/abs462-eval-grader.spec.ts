// Functional: ABS-462 — verify_test_prompts.py grades applicability, not just existence
//
// The eval grader is a pure CLI tool with no web-UI surface, so these tests
// drive it through spawnSync, matching abs203-test-prompts.spec.ts and
// abs286-compare-ab-runs.spec.ts.
//
// Every case here runs with `--corpus-json evals/fixtures/abs462_corpus_snapshot.json`,
// a verbatim slice of the real Regional Centre ingest, so no database or stack
// is required. The snapshot reproduces the live-DB grading of the committed
// 20260811T113204Z run exactly (7/7 citations, same verdict).
//
// What we verify:
//   1. Keyword coverage is a case-level score — a 2-turn case whose keywords
//      are split across turns scores 100%, not 50%.
//   2. expected_bylaw_references is graded: an answer citing a real-but-
//      different provision misses coverage and the substitute is reported.
//   3. expected_topics scores a correct prose answer at full marks, which
//      substring matching (the approach the ticket rules out) cannot do.
//   4. Re-grading the committed 20260811T113204Z run flags the 198(1)(d)
//      side-setback error with FAIL_APPLICABILITY, and the committed
//      verification artifact records that finding.

import { spawnSync } from "child_process";
import * as fs from "fs";
import * as os from "os";
import * as path from "path";
import { expect, test } from "../fixtures/test-env";

const REPO_ROOT = path.resolve(__dirname, "../../../");
const SCRIPT = path.join(REPO_ROOT, "scripts", "verify_test_prompts.py");
const VENV_PYTHON = path.join(REPO_ROOT, ".venv", "bin", "python");
const CORPUS = path.join(REPO_ROOT, "evals", "fixtures", "abs462_corpus_snapshot.json");
const COMMITTED_RUN = path.join(REPO_ROOT, "evals", "runs", "20260811T113204Z");

interface Grade {
  verdict: string;
  reasons: string[];
  keyword_rate: number | null;
  keyword_misses: string[];
  reference_rate: number | null;
  topic_rate: number | null;
  topic_misses: string[];
  citation_total: number;
  citation_found: number;
  citation_hallucinated: number;
  references: {
    misses: string[];
    unexpected: Array<{ kind: string; value: string }>;
    entries: Array<{ reference: string; resolved_in_corpus: boolean; cited: boolean }>;
  };
  applicability_findings: Array<{
    citation: string;
    section: string;
    clause: string;
    trigger_zones: string[];
    zones_in_answer: string[];
    clause_text: string;
    reason: string;
  }>;
}

interface VerifyRecord {
  id: string;
  grade: Grade;
  turns: Array<{ turn: number; skipped?: string }>;
}

function tmpDir(name: string): string {
  return fs.mkdtempSync(path.join(os.tmpdir(), `abs462-${name}-`));
}

/** Write a transcript with one assistant turn per text, then grade it. */
function gradeSynthetic(
  name: string,
  texts: string[],
  spec: Record<string, unknown>,
): { record: VerifyRecord; status: number; stderr: string } {
  const dir = tmpDir(name);
  const transcript = {
    id: "TC-001",
    title: `ABS-462 ${name}`,
    zone: "HR-1",
    turns: texts.map((assistant_text, i) => ({ turn: i + 1, assistant_text })),
    spec: { complexity: "simple", liability: "low", ...spec },
  };
  fs.writeFileSync(path.join(dir, "TC-001.json"), JSON.stringify(transcript));
  // --spec-source transcript: grade against the expectations written above,
  // not TC-001's entry in the live eval file.
  const run = spawnSync(
    VENV_PYTHON,
    [SCRIPT, dir, "--corpus-json", CORPUS, "--spec-source", "transcript"],
    { cwd: REPO_ROOT, encoding: "utf-8" },
  );
  const recordPath = path.join(dir, "verification", "TC-001.verify.json");
  expect(
    fs.existsSync(recordPath),
    `grader wrote no verification record\nstderr: ${run.stderr}`,
  ).toBe(true);
  return {
    record: JSON.parse(fs.readFileSync(recordPath, "utf-8")) as VerifyRecord,
    status: run.status ?? -1,
    stderr: run.stderr ?? "",
  };
}

// ─── 1. Keyword coverage is a case-level score ───────────────────────────────

test.describe("ABS-462 keywords — scored once per case, over the union of turns", () => {
  test("a 2-turn case with keywords split across turns scores 100%", () => {
    const { record, status } = gradeSynthetic(
      "kw-split",
      [
        "In HR-1 the rear setback is 3.0 m and you need a development permit.",
        "The side setback is 2.5 m.",
      ],
      {
        expected_answer_keywords: [
          "rear setback", "3.0 m", "development permit",
          "side setback", "2.5 m", "HR-1",
        ],
      },
    );
    expect(status).toBe(0);
    expect(record.grade.keyword_rate).toBe(1.0);
    expect(record.grade.keyword_misses).toEqual([]);
    // Per-turn scoring — the old behaviour — would have graded this 6/12.
    expect(record.grade.verdict).toBe("PASS");
  });

  test("keywords the conversation never says are still counted as misses", () => {
    const { record } = gradeSynthetic("kw-miss", ["The rear setback is 3.0 m."], {
      expected_answer_keywords: ["rear setback", "3.0 m", "side setback", "HR-1"],
    });
    expect(record.grade.keyword_rate).toBe(0.5);
    expect(record.grade.keyword_misses.sort()).toEqual(["HR-1", "side setback"]);
  });
});

// ─── 2. expected_bylaw_references are graded ─────────────────────────────────

test.describe("ABS-462 references — resolved against the corpus and the answer", () => {
  test("an answer citing a real-but-different provision misses coverage", () => {
    const { record } = gradeSynthetic(
      "ref-substitute",
      ["Your rear setback is governed by Section 200(1)(a), which sets the streetwall height."],
      {
        expected_answer_keywords: ["rear setback"],
        expected_bylaw_references: ["Section 199"],
      },
    );
    // Section 200 exists, so nothing is hallucinated — that is precisely why
    // the old scorer waved this through.
    expect(record.grade.citation_hallucinated).toBe(0);
    expect(record.grade.reference_rate).toBe(0);
    expect(record.grade.references.misses).toEqual(["Section 199"]);
    expect(record.grade.references.unexpected).toContainEqual({
      kind: "section",
      value: "200",
    });
    expect(record.grade.verdict).not.toBe("PASS");
    expect(record.grade.reasons.join(" ")).toContain("expected-reference coverage");
  });

  test("expected references are checked for existence in the corpus", () => {
    const { record } = gradeSynthetic(
      "ref-resolve",
      ["Section 198 and Section 9(1)(c) govern this."],
      {
        expected_answer_keywords: ["Section 198"],
        expected_bylaw_references: ["Section 198", "Section 9(1)(c)"],
      },
    );
    expect(record.grade.reference_rate).toBe(1.0);
    for (const entry of record.grade.references.entries) {
      expect(entry.resolved_in_corpus, `${entry.reference} must resolve`).toBe(true);
      expect(entry.cited, `${entry.reference} must be cited`).toBe(true);
    }
  });
});

// ─── 3. expected_topics match normalised tokens, never substrings ────────────

test.describe("ABS-462 topics — normalised token match", () => {
  const PROSE =
    "Section 9(1)(c) exempts uncovered structures under 0.6 m from needing a " +
    "development permit. Your rear setback must be 3.0 metres and the side " +
    "setbacks are 2.5 metres.";
  const TOPICS = ["rear_setback", "side_setback", "development_permit_exemption"];

  test("a correct prose answer scores full topic marks", () => {
    const { record } = gradeSynthetic("topics", [PROSE], {
      expected_answer_keywords: ["rear setback"],
      expected_topics: TOPICS,
    });
    expect(record.grade.topic_rate).toBe(1.0);
    expect(record.grade.topic_misses).toEqual([]);
  });

  test("substring matching — the approach ruled out — would score it zero", () => {
    // Guards the reason the token matcher exists: no prose answer will ever
    // contain the literal label.
    for (const topic of TOPICS) {
      expect(PROSE.toLowerCase()).not.toContain(topic);
    }
  });

  test("an answer that never discusses a topic misses it", () => {
    const { record } = gradeSynthetic("topics-miss", ["The rear setback is 3.0 m."], {
      expected_answer_keywords: ["rear setback"],
      expected_topics: TOPICS,
    });
    expect(record.grade.topic_misses.sort()).toEqual([
      "development_permit_exemption",
      "side_setback",
    ]);
  });
});

// ─── 4. The applicability check on the committed run ─────────────────────────

test.describe("ABS-462 applicability — the 198(1)(d) side-setback error", () => {
  test("re-grading the committed 20260811T113204Z run flags it", () => {
    // Copy the transcript out so the grader does not rewrite the committed
    // verification artifact as a side effect of the test run.
    const dir = tmpDir("regrade");
    fs.copyFileSync(
      path.join(COMMITTED_RUN, "TC-001.json"),
      path.join(dir, "TC-001.json"),
    );
    const run = spawnSync(VENV_PYTHON, [SCRIPT, dir, "--corpus-json", CORPUS], {
      cwd: REPO_ROOT,
      encoding: "utf-8",
    });
    expect(run.status, run.stderr).toBe(0);

    const record = JSON.parse(
      fs.readFileSync(path.join(dir, "verification", "TC-001.verify.json"), "utf-8"),
    ) as VerifyRecord;

    // Everything the old scorer could see still clears its bar: seven
    // citations, all real, and every scalar rate at or above threshold.
    expect(record.grade.citation_hallucinated).toBe(0);
    expect(record.grade.citation_found).toBe(record.grade.citation_total);
    expect(record.grade.citation_total).toBe(7);
    expect(record.grade.reference_rate).toBe(1.0);
    expect(record.grade.topic_rate).toBe(1.0);

    // Keyword coverage was 1.0 when this test landed. ABS-470 corrected
    // TC-001's expectations — s.198(1)(f) puts this lot's side yard at 2.5 m,
    // and the 3.0 m the corpus used to expect is the townhouse branch — so the
    // one keyword this answer never says is now scored as the miss it is.
    expect(record.grade.keyword_rate).toBe(0.833);
    expect(record.grade.keyword_misses).toEqual(["2.5 m"]);
    // Which changes nothing about why the applicability check exists: TC-001
    // is a `simple` case, so the keyword bar is 0.80 (KEYWORD_PASS_BAR), and
    // 0.833 still clears it. Every scalar the grader reports would pass this
    // answer. Only the applicability finding below fails it.

    // And the answer is still wrong.
    expect(record.grade.verdict).toBe("FAIL_APPLICABILITY");
    expect(record.grade.applicability_findings.length).toBe(1);
    const finding = record.grade.applicability_findings[0];
    expect(finding.citation).toBe("198(1)(d)");
    expect(finding.trigger_zones.sort()).toEqual(
      ["CEN-1", "CEN-2", "COR", "DD", "DH"],
    );
    expect(finding.zones_in_answer).toContain("HR-1");
    expect(finding.clause_text).toContain("0.0 metre");
  });

  test("the committed verification artifact records the finding", () => {
    const record = JSON.parse(
      fs.readFileSync(
        path.join(COMMITTED_RUN, "verification", "TC-001.verify.json"),
        "utf-8",
      ),
    ) as VerifyRecord;
    expect(record.grade.verdict).toBe("FAIL_APPLICABILITY");
    expect(
      record.grade.applicability_findings.map((f) => f.citation),
    ).toEqual(["198(1)(d)"]);

    const summary = JSON.parse(
      fs.readFileSync(path.join(COMMITTED_RUN, "verification", "SUMMARY.json"), "utf-8"),
    ) as Array<{ id: string; verdict: string; inapplicable: number; kw_rate: number }>;
    const tc001 = summary.find((e) => e.id === "TC-001");
    expect(tc001?.verdict).toBe("FAIL_APPLICABILITY");
    expect(tc001?.inapplicable).toBe(1);
    // The same conversation used to grade PARTIAL at 67% keyword coverage.
    // This is the artifact as it was written on 2026-08-11, against the corpus
    // of that date; runs under evals/runs/ record what the grader said at the
    // time and are not rewritten. Re-grading the same transcript today scores
    // 0.833 — see the test above — because ABS-470 corrected the keyword the
    // answer gets wrong. The two numbers disagreeing is the point.
    expect(tc001?.kw_rate).toBe(1.0);
  });

  test("an answer that engages the clause's condition is not flagged", () => {
    const { record } = gradeSynthetic(
      "applicability-ok",
      [
        "Section 199(1)(a) requires 6.0 m where the rear lot line abuts an ER-3, " +
          "ER-2, ER-1, CH-2, CH-1, PCF, or RPK zone. Your neighbours are HR-1, so " +
          "199(1)(b) — 3.0 metres elsewhere — governs.",
      ],
      {
        expected_answer_keywords: ["3.0 m"],
        expected_bylaw_references: ["Section 199"],
      },
    );
    expect(record.grade.applicability_findings).toEqual([]);
    expect(record.grade.verdict).toBe("PASS");
  });
});
