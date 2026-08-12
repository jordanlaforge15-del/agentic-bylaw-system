// Functional: ABS-468 — the eval's two tiers of evidence stay apart
//
// Every expectation in evals/regional_centre_test_prompts.json — question,
// persona, expected keywords, expected references — was authored by `claude -p`,
// and the system under test is a Claude model. A generated case that passes
// establishes that the advisor agrees with what a model guessed the answer was,
// not that the answer is correct under the by-law.
//
// evals/golden/golden_cases.json is the other tier: six cases whose correct
// answers a qualified human records. This spec pins the properties that make
// the distinction load-bearing rather than decorative:
//
//   1. The committed golden file validates, and its selection still spans the
//      zone / liability / answer-shape range the ticket required.
//   2. It ships unattested, and an unattested entry can never pass — it holds
//      the production-deploy gate closed and `--gate` exits non-zero.
//   3. An attested case grades against the human's answer: a fluent answer
//      citing the right section still fails when it asserts something the
//      reviewer marked wrong.
//   4. The two tiers write separate artifacts with disjoint verdict
//      vocabularies, so no reader can sum them into one pass rate.
//
// Like abs462/abs463, this drives the CLI through spawnSync against the
// committed corpus snapshot — no stack, no database.

import { spawnSync } from "child_process";
import * as fs from "fs";
import * as os from "os";
import * as path from "path";
import { expect, test } from "../fixtures/test-env";

const REPO_ROOT = path.resolve(__dirname, "../../../");
const SCRIPT = path.join(REPO_ROOT, "scripts", "verify_golden_cases.py");
const GENERATED_SCRIPT = path.join(REPO_ROOT, "scripts", "verify_test_prompts.py");
const VENV_PYTHON = path.join(REPO_ROOT, ".venv", "bin", "python");
const CORPUS = path.join(REPO_ROOT, "evals", "fixtures", "abs462_corpus_snapshot.json");
const GOLDEN_FILE = path.join(REPO_ROOT, "evals", "golden", "golden_cases.json");

type Attestation = {
  status: string;
  attested_by: { name: string; credential: string } | null;
  attested_on: string | null;
  correct_answer: string | null;
  governing_provisions: Array<{ reference: string; holding: string }>;
  must_state: Array<{ id: string; description: string; any_of: string[] }>;
  must_not_state: Array<{ id: string; description: string; any_of: string[] }>;
};

type GoldenCase = {
  case_id: string;
  zone: string;
  liability: string;
  answer_shape: string;
  selection_rationale: string;
  question_for_reviewer: string;
  attestation: Attestation;
};

type GoldenSummary = {
  evidence_tier: string;
  gate: { gates: string; open: boolean; blockers: string[] };
  cases: Array<{ case_id: string; verdict: string; reasons: string[] }>;
};

function loadGolden(): { cases: GoldenCase[]; gate: Record<string, string> } {
  expect(fs.existsSync(GOLDEN_FILE), `${GOLDEN_FILE} must exist`).toBe(true);
  return JSON.parse(fs.readFileSync(GOLDEN_FILE, "utf-8"));
}

function tmpDir(name: string): string {
  return fs.mkdtempSync(path.join(os.tmpdir(), `abs468-${name}-`));
}

/** Grade one synthetic transcript against a bespoke golden file. */
function gradeGolden(
  name: string,
  goldenCases: unknown[],
  transcriptText: string,
  caseId = "TC-001",
): { summary: GoldenSummary | null; status: number; stderr: string } {
  const dir = tmpDir(name);
  fs.writeFileSync(
    path.join(dir, `${caseId}.json`),
    JSON.stringify({
      id: caseId,
      turns: [{ turn: 1, assistant_text: transcriptText }],
    }),
  );
  const goldenPath = path.join(dir, "golden.json");
  fs.writeFileSync(goldenPath, JSON.stringify({ schema_version: 1, cases: goldenCases }));
  const run = spawnSync(
    VENV_PYTHON,
    [SCRIPT, dir, "--golden", goldenPath, "--corpus-json", CORPUS, "--gate"],
    { cwd: REPO_ROOT, encoding: "utf-8" },
  );
  const summaryPath = path.join(dir, "verification", "GOLDEN_SUMMARY.json");
  return {
    summary: fs.existsSync(summaryPath)
      ? (JSON.parse(fs.readFileSync(summaryPath, "utf-8")) as GoldenSummary)
      : null,
    status: run.status ?? -1,
    stderr: run.stderr ?? "",
  };
}

/** An attested entry for TC-001's real side-setback question. */
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
      correct_answer: "2.5 m applies; the 0.0 m clause is conditional.",
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

// ─── 1. The committed subset ─────────────────────────────────────────────────

test.describe("ABS-468 the committed golden subset", () => {
  test("validates via --check", () => {
    const run = spawnSync(VENV_PYTHON, [SCRIPT, "--check"], {
      cwd: REPO_ROOT,
      encoding: "utf-8",
    });
    expect(run.status, run.stderr).toBe(0);
    expect(run.stderr).toContain("awaiting a qualified human");
  });

  test("spans the zone, liability and answer-shape range the ticket required", () => {
    // Six determinate low-liability lookups would be cheap to attest and would
    // measure nothing: the answers most likely to be wrong are the ones where
    // the by-law does not give a number.
    const { cases } = loadGolden();
    expect(cases.length).toBeGreaterThanOrEqual(5);
    expect(cases.length).toBeLessThanOrEqual(6);
    expect(new Set(cases.map((c) => c.zone)).size).toBe(cases.length);
    expect(new Set(cases.map((c) => c.liability))).toEqual(
      new Set(["low", "medium", "high"]),
    );
    const shapes = new Set(cases.map((c) => c.answer_shape));
    expect(
      shapes.has("refusal") || shapes.has("depends"),
      "at least one case whose correct answer is a refusal or a 'depends'",
    ).toBe(true);
    for (const c of cases) {
      expect(c.selection_rationale.length, `${c.case_id} rationale`).toBeGreaterThan(40);
      expect(c.question_for_reviewer.length, `${c.case_id} question`).toBeGreaterThan(40);
    }
  });

  test("every golden case_id exists in the generated eval", () => {
    const generated = JSON.parse(
      fs.readFileSync(
        path.join(REPO_ROOT, "evals", "regional_centre_test_prompts.json"),
        "utf-8",
      ),
    ) as Array<{ id: string }>;
    const known = new Set(generated.map((c) => c.id));
    for (const c of loadGolden().cases) {
      expect(known.has(c.case_id), `${c.case_id} is not a case in the eval file`).toBe(true);
    }
  });

  test("no answer has been filled in by anyone unqualified", () => {
    // The artifact is worthless the moment a model or an engineer on this
    // project writes the "correct" answer — that is the defect ABS-468 is
    // about. When a professional does attest, this expectation changes and the
    // reviewer's name lands in attested_by.
    for (const c of loadGolden().cases) {
      expect(c.attestation.status, `${c.case_id}`).toBe("unattested");
      expect(c.attestation.correct_answer).toBeNull();
      expect(c.attestation.governing_provisions).toEqual([]);
    }
  });

  test("the file records what it gates and that the tiers are never summed", () => {
    const { gate } = loadGolden();
    expect(gate.blocks).toBe("production_deploy");
    expect(gate.advisory).toContain("regional_centre_test_prompts.json");
    expect(gate.never.toLowerCase()).toContain("never");
  });
});

// ─── 2. Unattested holds the gate closed ─────────────────────────────────────

test.describe("ABS-468 unattested is not a pass", () => {
  test("an unattested case grades UNATTESTED however good the answer is", () => {
    const unattested = {
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
    const { summary, status } = gradeGolden(
      "unattested",
      [unattested],
      "Under Section 198 the side setback is 2.5 m.",
    );
    expect(summary!.cases[0].verdict).toBe("UNATTESTED");
    expect(summary!.gate.open).toBe(false);
    expect(summary!.gate.gates).toBe("production_deploy");
    expect(status, "--gate must exit non-zero while anything is unattested").toBe(1);
  });

  test("the committed subset closes the gate today", () => {
    const dir = tmpDir("committed");
    fs.writeFileSync(
      path.join(dir, "TC-001.json"),
      JSON.stringify({
        id: "TC-001",
        turns: [{ turn: 1, assistant_text: "Section 198 gives 2.5 m." }],
      }),
    );
    const run = spawnSync(
      VENV_PYTHON,
      [SCRIPT, dir, "--corpus-json", CORPUS, "--gate"],
      { cwd: REPO_ROOT, encoding: "utf-8" },
    );
    expect(run.status).toBe(1);
    expect(run.stderr).toContain("Gate: CLOSED");
  });
});

// ─── 3. Grading against a human's answer ─────────────────────────────────────

test.describe("ABS-468 an attested case is graded against the human's answer", () => {
  test("a correct, sourced answer passes and opens the gate", () => {
    const { summary, status } = gradeGolden(
      "correct",
      [attestedCase()],
      "Under Section 198 the side setback for your lot is 2.5 m.",
    );
    expect(summary!.cases[0].verdict).toBe("GOLDEN_PASS");
    expect(summary!.gate.open).toBe(true);
    expect(status).toBe(0);
  });

  test("a confidently wrong answer fails even though its citation is real", () => {
    // The ABS-462 regression, graded against a professional's answer instead of
    // a model's. Citation-existence passes this; reference coverage passes it.
    // Only "0.0 m is the wrong answer for this lot" catches it.
    const { summary, status } = gradeGolden(
      "confidently-wrong",
      [attestedCase()],
      "Section 198(1)(d) gives you a 0.0 m side setback — you can build to the line.",
    );
    expect(summary!.cases[0].verdict).toBe("GOLDEN_FAIL");
    expect(summary!.cases[0].reasons.join(" ")).toContain("marked wrong");
    expect(status).toBe(1);
  });

  test("the right answer without the governing provision is PARTIAL, not PASS", () => {
    const { summary, status } = gradeGolden(
      "unsourced",
      [attestedCase()],
      "Your side setback is 2.5 m.",
    );
    expect(summary!.cases[0].verdict).toBe("GOLDEN_PARTIAL");
    expect(status, "PARTIAL does not open the deploy gate").toBe(1);
  });

  test("a 'depends' case rejects a flat answer and accepts a conditional one", () => {
    const depends = attestedCase({ answer_shape: "depends" });
    const flat = gradeGolden(
      "depends-flat",
      [depends],
      "Section 198. The side setback is 2.5 m.",
    );
    expect(flat.summary!.cases[0].verdict).toBe("GOLDEN_FAIL");
    expect(flat.summary!.cases[0].reasons.join(" ")).toContain("unconditional");

    const conditional = gradeGolden(
      "depends-conditional",
      [depends],
      "Section 198. It depends on what your lot line abuts; for your lot, 2.5 m.",
    );
    expect(conditional.summary!.cases[0].verdict).toBe("GOLDEN_PASS");
  });
});

// ─── 4. The tiers cannot be merged ───────────────────────────────────────────

test.describe("ABS-468 the two tiers stay apart", () => {
  test("golden and generated write different files with different verdicts", () => {
    const dir = tmpDir("tiers");
    const transcript = {
      id: "TC-001",
      title: "two-tier",
      zone: "HR-1",
      turns: [{ turn: 1, assistant_text: "Under Section 198 the side setback is 2.5 m." }],
      spec: {
        complexity: "simple",
        liability: "low",
        expected_answer_keywords: ["2.5 m"],
        expected_bylaw_references: ["Section 198"],
        expected_topics: ["side_setback"],
      },
    };
    fs.writeFileSync(path.join(dir, "TC-001.json"), JSON.stringify(transcript));
    const goldenPath = path.join(dir, "golden.json");
    fs.writeFileSync(
      goldenPath,
      JSON.stringify({ schema_version: 1, cases: [attestedCase()] }),
    );

    const generated = spawnSync(
      VENV_PYTHON,
      [GENERATED_SCRIPT, dir, "--corpus-json", CORPUS, "--spec-source", "transcript"],
      { cwd: REPO_ROOT, encoding: "utf-8" },
    );
    expect(generated.status, generated.stderr).toBe(0);
    const golden = spawnSync(
      VENV_PYTHON,
      [SCRIPT, dir, "--golden", goldenPath, "--corpus-json", CORPUS],
      { cwd: REPO_ROOT, encoding: "utf-8" },
    );
    expect(golden.status, golden.stderr).toBe(0);

    const generatedSummary = JSON.parse(
      fs.readFileSync(path.join(dir, "verification", "SUMMARY.json"), "utf-8"),
    ) as Array<{ verdict: string; evidence_tier: string }>;
    const goldenSummary = JSON.parse(
      fs.readFileSync(path.join(dir, "verification", "GOLDEN_SUMMARY.json"), "utf-8"),
    ) as GoldenSummary;

    // Separate files, and each row says which tier it belongs to.
    expect(generatedSummary[0].evidence_tier).toBe("generated");
    expect(goldenSummary.evidence_tier).toBe("human_validated");

    // Disjoint vocabularies: a script reading both cannot count a golden row
    // into a generated pass rate, or vice versa, even by mistake.
    const generatedVerdicts = new Set([
      "PASS", "PARTIAL", "FAIL", "FAIL_HALLUCINATION", "FAIL_APPLICABILITY", "NO_DATA",
    ]);
    expect(generatedVerdicts.has(generatedSummary[0].verdict)).toBe(true);
    expect(generatedVerdicts.has(goldenSummary.cases[0].verdict)).toBe(false);
    expect(goldenSummary.cases[0].verdict.startsWith("GOLDEN_")).toBe(true);
  });

  test("the readiness report keeps the gating and advisory numbers apart", () => {
    const dir = tmpDir("report");
    fs.writeFileSync(path.join(dir, "SUMMARY.json"), JSON.stringify([{ id: "TC-001" }]));
    fs.mkdirSync(path.join(dir, "verification"));
    fs.writeFileSync(
      path.join(dir, "verification", "SUMMARY.json"),
      JSON.stringify([
        {
          id: "TC-001",
          evidence_tier: "generated",
          verdict: "PASS",
          complexity: "simple",
          zone: "HR-1",
          liability: "low",
          kw_rate: 1,
          citation_found: 1,
          citation_total: 1,
          hallucinated: 0,
          reasons: [],
        },
      ]),
    );
    const report = spawnSync(
      VENV_PYTHON,
      [path.join(REPO_ROOT, "scripts", "build_readiness_report.py"), dir],
      { cwd: REPO_ROOT, encoding: "utf-8" },
    );
    expect(report.status, report.stderr).toBe(0);
    const md = fs.readFileSync(path.join(dir, "REPORT.md"), "utf-8");
    // A perfect generated sweep with no golden grading is not a green light.
    expect(md).toContain("Generated-case verdict (advisory)");
    expect(md).toContain("Golden subset — human-validated (gating)");
    expect(md).toContain("The deploy gate is CLOSED");
  });
});
