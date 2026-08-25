// Functional: ABS-516 — one command grades a run, and only the golden tier gates
//
// There were two graders for one suite and they disagreed. On the same eight
// transcripts, scripts/verify_test_prompts.py reported 5 PASS / 3 PARTIAL /
// 0 FAIL and scripts/verify_golden_cases.py reported 3 PASS / 1 PARTIAL /
// 4 FAIL. Nothing forced a caller to run both and neither output mentioned the
// other, so running the advisory one and reporting "the tests pass" was the
// default outcome, not an edge case — it is what happened in
// the zone-typology-all8 run on the docs/zone-typology-test-questions branch.
//
// The separation of evidence is deliberate (evals/golden/README.md): a
// model-authored expectation and a professional's answer must never be summed.
// The defect was two entry points. scripts/verify_run.py is the one entry
// point, and this spec pins the properties that make it load-bearing:
//
//   1. One invocation grades both tiers and writes both tiers' artifacts.
//   2. The golden tier is printed first and labelled as the one that gates.
//   3. Exit status is the golden tier's alone — a perfect advisory sweep cannot
//      open the gate, and a failing advisory sweep cannot close it.
//   4. Unattested golden entries are announced loudly, not folded into "0 FAIL".
//   5. Nothing sums the tiers, in the report or in RUN_SUMMARY.json.
//   6. Running the advisory tier by itself warns that it has not graded the run.
//   7. The docs point at the single entry point.
//
// Like abs462/abs468, this drives the CLI through spawnSync against the
// committed corpus snapshot — no stack, no database.

import { spawnSync } from "child_process";
import * as fs from "fs";
import * as os from "os";
import * as path from "path";
import { expect, test } from "../fixtures/test-env";

const REPO_ROOT = path.resolve(__dirname, "../../../");
const ENTRY_POINT = path.join(REPO_ROOT, "scripts", "verify_run.py");
const ADVISORY_SCRIPT = path.join(REPO_ROOT, "scripts", "verify_test_prompts.py");
const VENV_PYTHON = path.join(REPO_ROOT, ".venv", "bin", "python");
const CORPUS = path.join(REPO_ROOT, "evals", "fixtures", "abs462_corpus_snapshot.json");

const RIGHT_ANSWER = "Under Section 198 the side setback for your lot is 2.5 m.";
// Fluent, real citation, right topic, wrong answer: the shape the generated
// grader is structurally unable to fail, because the model that authored its
// expectations does not know which wrong answer a model is inclined to give.
const WRONG_ANSWER = "Under Section 198 your side setback is 0.0 m, so build to the line.";

type RunSummary = {
  entry_point: string;
  note: string;
  gating: {
    evidence_tier: string;
    counts: Record<string, number>;
    gate: { open: boolean; blockers: string[] };
    cases: Array<{ case_id: string; verdict: string }>;
  };
  advisory: {
    evidence_tier: string;
    gates: null;
    counts: Record<string, number>;
    error: string | null;
    cases: Array<{ id: string; verdict: string }>;
  };
  gate_open: boolean;
  exit_driven_by: string;
};

function tmpDir(name: string): string {
  return fs.mkdtempSync(path.join(os.tmpdir(), `abs516-${name}-`));
}

/** An attested golden entry for TC-001's side-setback question. */
function attestedCase(overrides: Record<string, unknown> = {}): Record<string, unknown> {
  return {
    case_id: "TC-001",
    zone: "HR-1",
    liability: "low",
    answer_shape: "determinate",
    selection_rationale: "synthetic",
    question_for_reviewer: "What side setback governs?",
    attestation: {
      status: "attested",
      attested_by: { name: "A. Reviewer", credential: "MCIP, LPP" },
      attested_on: "2026-08-20",
      method: "read the by-law",
      correct_answer: "2.5 m applies.",
      governing_provisions: [
        { reference: "Section 198", holding: "side setback standards for HR-1" },
      ],
      must_state: [
        {
          id: "side-setback",
          description: "gives 2.5 m as the side setback",
          any_of: ["2.5 m", "2.5 metres"],
        },
      ],
      must_not_state: [
        {
          id: "no-zero-setback",
          description: "must not tell this owner the side setback is zero",
          any_of: ["0.0 m", "no side setback"],
        },
      ],
    },
    ...overrides,
  };
}

const UNATTESTED_CASE = {
  case_id: "TC-001",
  zone: "HR-1",
  liability: "low",
  answer_shape: "determinate",
  selection_rationale: "synthetic",
  question_for_reviewer: "What side setback governs?",
  attestation: {
    status: "unattested",
    attested_by: null,
    attested_on: null,
    correct_answer: null,
    governing_provisions: [],
    must_state: [],
    must_not_state: [],
  },
};

/**
 * Grade a one-case run through the single entry point.
 *
 * `keywords` drives the *advisory* tier only, so a test can make the two tiers
 * disagree on purpose — which is the whole subject of this spec.
 */
function verifyRun(
  name: string,
  answer: string,
  goldenCases: unknown[],
  keywords: string[] = ["Section 198"],
): { summary: RunSummary | null; status: number; stdout: string; stderr: string; dir: string } {
  const dir = tmpDir(name);
  fs.writeFileSync(
    path.join(dir, "TC-001.json"),
    JSON.stringify({
      id: "TC-001",
      title: "side setback",
      zone: "HR-1",
      turns: [{ turn: 1, assistant_text: answer }],
      spec: {
        complexity: "simple",
        liability: "low",
        expected_answer_keywords: keywords,
        expected_bylaw_references: ["Section 198"],
        expected_topics: ["side_setback"],
      },
    }),
  );
  const goldenPath = path.join(dir, "golden.json");
  fs.writeFileSync(goldenPath, JSON.stringify({ schema_version: 1, cases: goldenCases }));
  const run = spawnSync(
    VENV_PYTHON,
    [
      ENTRY_POINT,
      dir,
      "--golden",
      goldenPath,
      "--corpus-json",
      CORPUS,
      "--spec-source",
      "transcript",
    ],
    { cwd: REPO_ROOT, encoding: "utf-8" },
  );
  const summaryPath = path.join(dir, "verification", "RUN_SUMMARY.json");
  return {
    summary: fs.existsSync(summaryPath)
      ? (JSON.parse(fs.readFileSync(summaryPath, "utf-8")) as RunSummary)
      : null,
    status: run.status ?? -1,
    stdout: run.stdout ?? "",
    stderr: run.stderr ?? "",
    dir,
  };
}

// ─── 1. One command, both tiers ──────────────────────────────────────────────

test.describe("ABS-516 one command grades a run end to end", () => {
  test("a single invocation writes both tiers' artifacts", () => {
    const { dir, status, stderr } = verifyRun("both", RIGHT_ANSWER, [attestedCase()]);
    expect(status, stderr).toBe(0);
    const verification = path.join(dir, "verification");
    for (const artifact of [
      "GOLDEN_SUMMARY.json",
      "SUMMARY.json",
      "TC-001.golden.json",
      "TC-001.verify.json",
      "RUN_SUMMARY.json",
    ]) {
      expect(fs.existsSync(path.join(verification, artifact)), artifact).toBe(true);
    }
  });

  test("the report labels which tier is authoritative and prints it first", () => {
    const { stdout } = verifyRun("labels", RIGHT_ANSWER, [attestedCase()]);
    const goldenAt = stdout.indexOf("GOLDEN (human-attested, gates deploy)");
    const generatedAt = stdout.indexOf("GENERATED (model-authored, advisory)");
    expect(goldenAt).toBeGreaterThanOrEqual(0);
    expect(generatedAt).toBeGreaterThan(goldenAt);
    expect(stdout).toContain("[GATE: OPEN]");
    expect(stdout).toContain("gates nothing");
    expect(stdout).toContain("Exit status is set by the golden tier alone");
  });
});

// ─── 2. Only the golden tier moves the gate ──────────────────────────────────

test.describe("ABS-516 the gate is the golden tier's alone", () => {
  test("an advisory sweep with zero failures cannot open a closed gate", () => {
    // The zone-typology-all8 outcome, reproduced: the answer is confidently
    // wrong, the advisory grader sees nothing wrong with it, and the run must
    // still not read as passing.
    const { summary, status, stdout } = verifyRun("advisory-blind", WRONG_ANSWER, [
      attestedCase(),
    ]);
    expect(summary!.advisory.counts.FAIL, "the advisory tier sees no problem").toBe(0);
    expect(summary!.advisory.counts.PASS).toBe(1);
    expect(summary!.gating.counts.FAIL).toBe(1);
    expect(summary!.gate_open).toBe(false);
    expect(status, "the golden tier closes the gate").toBe(1);
    expect(stdout).toContain("DEPLOY GATE: CLOSED");
  });

  test("a failing advisory sweep cannot close an open gate", () => {
    // Keywords no correct answer would contain: the advisory tier fails while
    // the human-attested tier passes. The professional's answer is what counts.
    const { summary, status } = verifyRun(
      "advisory-noise",
      RIGHT_ANSWER,
      [attestedCase()],
      ["quonset hut", "aerodrome"],
    );
    expect(summary!.advisory.counts.PASS).toBe(0);
    expect(summary!.gating.counts.PASS).toBe(1);
    expect(summary!.gate_open).toBe(true);
    expect(status, "advisory results gate nothing").toBe(0);
    expect(summary!.exit_driven_by).toBe("human_validated");
    expect(summary!.advisory.gates).toBeNull();
  });
});

// ─── 3. Unattested is loud ───────────────────────────────────────────────────

test.describe("ABS-516 an unattested golden subset is never silently passing", () => {
  test("the report says so in its own block and the gate stays closed", () => {
    const { summary, status, stdout } = verifyRun("unattested", RIGHT_ANSWER, [
      UNATTESTED_CASE,
    ]);
    expect(summary!.gating.counts.UNATTESTED).toBe(1);
    expect(summary!.advisory.counts.PASS, "the advisory tier passed").toBe(1);
    expect(stdout).toContain("1 of 1 golden entries are UNATTESTED");
    expect(stdout).toContain("demonstrated nothing about");
    expect(summary!.gate_open).toBe(false);
    expect(status).toBe(1);
  });

  test("the committed golden subset closes the gate today", () => {
    const dir = tmpDir("committed");
    fs.writeFileSync(
      path.join(dir, "TC-001.json"),
      JSON.stringify({
        id: "TC-001",
        turns: [{ turn: 1, assistant_text: "Section 198 gives 2.5 m." }],
        spec: { complexity: "simple", liability: "low" },
      }),
    );
    const run = spawnSync(
      VENV_PYTHON,
      [ENTRY_POINT, dir, "--corpus-json", CORPUS, "--spec-source", "transcript"],
      { cwd: REPO_ROOT, encoding: "utf-8" },
    );
    expect(run.status, run.stderr).toBe(1);
    expect(run.stdout).toContain("UNATTESTED");
    expect(run.stdout).toContain("DEPLOY GATE: CLOSED");
  });
});

// ─── 4. The tiers are never summed ───────────────────────────────────────────

test.describe("ABS-516 the two tiers are reported, never merged", () => {
  test("RUN_SUMMARY.json keeps them under separate keys with no total", () => {
    const { summary, stdout } = verifyRun("no-total", RIGHT_ANSWER, [attestedCase()]);
    expect(summary!.gating.evidence_tier).toBe("human_validated");
    expect(summary!.advisory.evidence_tier).toBe("generated");
    expect(summary!.note.toLowerCase()).toContain("never summed");
    for (const forbidden of ["counts", "total", "totals", "verdict", "pass_rate"]) {
      expect(Object.keys(summary!), `top-level ${forbidden}`).not.toContain(forbidden);
    }
    // One PASS in each tier, and nothing anywhere reads "2 PASS".
    expect(summary!.gating.counts.PASS).toBe(1);
    expect(summary!.advisory.counts.PASS).toBe(1);
    expect(stdout).not.toContain("2 PASS");
  });

  test("the verdict vocabularies stay disjoint across the tiers", () => {
    const { summary } = verifyRun("vocab", RIGHT_ANSWER, [attestedCase()]);
    expect(summary!.gating.cases[0].verdict).toBe("GOLDEN_PASS");
    expect(summary!.advisory.cases[0].verdict).toBe("PASS");
  });
});

// ─── 5. The advisory tier warns when run alone ───────────────────────────────

test.describe("ABS-516 the advisory grader says what it is not", () => {
  test("running verify_test_prompts.py alone prints a prominent warning", () => {
    const dir = tmpDir("advisory-alone");
    fs.writeFileSync(
      path.join(dir, "TC-001.json"),
      JSON.stringify({
        id: "TC-001",
        turns: [{ turn: 1, assistant_text: RIGHT_ANSWER }],
        spec: {
          complexity: "simple",
          liability: "low",
          expected_answer_keywords: ["Section 198"],
          expected_bylaw_references: ["Section 198"],
          expected_topics: ["side_setback"],
        },
      }),
    );
    const run = spawnSync(
      VENV_PYTHON,
      [ADVISORY_SCRIPT, dir, "--corpus-json", CORPUS, "--spec-source", "transcript"],
      { cwd: REPO_ROOT, encoding: "utf-8" },
    );
    expect(run.status, run.stderr).toBe(0);
    expect(run.stderr).toContain("ADVISORY TIER ONLY");
    expect(run.stderr).toContain("scripts/verify_run.py");
    // No GOLDEN_SUMMARY.json — running this alone has not graded the run, and
    // the warning is the only thing standing between that and "it passed".
    expect(fs.existsSync(path.join(dir, "verification", "GOLDEN_SUMMARY.json"))).toBe(false);
  });
});

// ─── 6. Usage errors are refusals, not verdicts ──────────────────────────────

test.describe("ABS-516 an ungradeable run does not produce a verdict", () => {
  test("a missing run directory exits 2", () => {
    const run = spawnSync(
      VENV_PYTHON,
      [ENTRY_POINT, path.join(os.tmpdir(), "abs516-does-not-exist"), "--corpus-json", CORPUS],
      { cwd: REPO_ROOT, encoding: "utf-8" },
    );
    expect(run.status).toBe(2);
    expect(run.stderr).toContain("Run dir not found");
  });

  test("a malformed golden file exits 2 rather than half-grading", () => {
    const dir = tmpDir("bad-golden");
    fs.writeFileSync(
      path.join(dir, "TC-001.json"),
      JSON.stringify({ id: "TC-001", turns: [{ turn: 1, assistant_text: RIGHT_ANSWER }] }),
    );
    const goldenPath = path.join(dir, "golden.json");
    // attested, but with the reviewer's answer missing: reads as ground truth,
    // grades as nothing.
    fs.writeFileSync(
      goldenPath,
      JSON.stringify({
        schema_version: 1,
        cases: [attestedCase({ answer_shape: "not-a-shape" })],
      }),
    );
    const run = spawnSync(
      VENV_PYTHON,
      [ENTRY_POINT, dir, "--golden", goldenPath, "--corpus-json", CORPUS],
      { cwd: REPO_ROOT, encoding: "utf-8" },
    );
    expect(run.status).toBe(2);
    expect(run.stderr).toContain("answer_shape");
    expect(fs.existsSync(path.join(dir, "verification", "RUN_SUMMARY.json"))).toBe(false);
  });
});

// ─── 7. The docs point at the one entry point ────────────────────────────────

test.describe("ABS-516 the documentation names one way to grade a run", () => {
  test("evals/golden/README.md and docs/TEST_PROMPT_GENERATION.md point at verify_run.py", () => {
    for (const doc of [
      path.join(REPO_ROOT, "evals", "golden", "README.md"),
      path.join(REPO_ROOT, "docs", "TEST_PROMPT_GENERATION.md"),
    ]) {
      const text = fs.readFileSync(doc, "utf-8");
      expect(text, `${doc} must document the single entry point`).toContain(
        "scripts/verify_run.py",
      );
    }
  });
});
