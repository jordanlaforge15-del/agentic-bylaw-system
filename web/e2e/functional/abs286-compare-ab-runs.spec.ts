// Functional: ABS-286 — compare_ab_runs.py CLI validation
//
// The A/B comparison script (scripts/compare_ab_runs.py) is a pure CLI tool —
// it has no web-UI surface. These tests validate its behaviour using spawnSync,
// matching the pattern from abs203-test-prompts.spec.ts.
//
// What we verify (no live stack required):
//   1. --help exits 0 and prints usage text.
//   2. A well-formed pair of synthetic run directories produces a Markdown
//      report with the expected section headers, a cost-ratio line, and a
//      verdict recommendation.
//   3. --output-md writes the report to the requested path.
//   4. The script exits non-zero when a run directory contains no TC-*.json
//      transcripts.
//
// Pricing cross-check embedded in test 2:
//   Opus 4.5  = $15/MTok input, $75/MTok output
//   Sonnet 4.6 = $3/MTok input, $15/MTok output  (5× cheaper)
//   With identical token counts, the candidate (Sonnet) is ~5× cheaper.
//   The "SWITCH TO SONNET" verdict additionally requires quality data in a
//   `verification/TC-*.verify.json` subdirectory (see test for SWITCH verdict below).

import { spawnSync } from "child_process";
import * as fs from "fs";
import * as os from "os";
import * as path from "path";
import { expect, test } from "../fixtures/test-env";

const REPO_ROOT = path.resolve(__dirname, "../../../");
const SCRIPT = path.join(REPO_ROOT, "scripts", "compare_ab_runs.py");
const VENV_PYTHON = path.join(REPO_ROOT, ".venv", "bin", "python");

function runScript(
  ...args: string[]
): { stdout: string; stderr: string; status: number } {
  const result = spawnSync(VENV_PYTHON, [SCRIPT, ...args], {
    cwd: REPO_ROOT,
    encoding: "utf-8",
  });
  return {
    stdout: result.stdout ?? "",
    stderr: result.stderr ?? "",
    status: result.status ?? -1,
  };
}

// ── Helpers to build synthetic run directories ────────────────────────────────

/** Minimal TC-NNN.json transcript that the script can ingest. */
function makeTranscript(
  id: string,
  model: string,
  inputTokens: number,
  outputTokens: number,
  iterations: number,
  terminatedReason = "max_iterations",
): object {
  return {
    id,
    title: `Synthetic case ${id}`,
    complexity: "simple",
    liability: "low",
    model,
    turns: [
      {
        tool_loop_metrics: {
          iterations,
          terminated_reason: terminatedReason,
          total_usage: {
            input_tokens: inputTokens,
            output_tokens: outputTokens,
            cache_creation_input_tokens: 0,
            cache_read_input_tokens: 0,
          },
        },
        wall_time_s: 4.2,
      },
    ],
  };
}

function writeSyntheticRun(
  dir: string,
  model: string,
  caseDefs: Array<{ id: string; input: number; output: number; iters: number }>,
): void {
  fs.mkdirSync(dir, { recursive: true });
  for (const c of caseDefs) {
    const tc = makeTranscript(c.id, model, c.input, c.output, c.iters);
    fs.writeFileSync(
      path.join(dir, `${c.id}.json`),
      JSON.stringify(tc, null, 2),
      "utf-8",
    );
  }
}

// ── Tests ─────────────────────────────────────────────────────────────────────

test.describe("compare_ab_runs.py — CLI validation (no server required)", () => {
  test("--help exits 0 and shows usage", () => {
    const { stdout, stderr, status } = runScript("--help");
    expect(status).toBe(0);
    const combined = stdout + stderr;
    expect(combined).toContain("--baseline");
    expect(combined).toContain("--candidate");
  });

  test("exits non-zero when baseline directory has no TC-*.json files", () => {
    const tmp = fs.mkdtempSync(path.join(os.tmpdir(), "abs286-empty-"));
    const { status, stderr } = runScript(
      "--baseline",
      tmp,
      "--candidate",
      tmp,
    );
    expect(status).not.toBe(0);
    expect(stderr).toMatch(/no TC-\*\.json/i);
    fs.rmSync(tmp, { recursive: true, force: true });
  });

  test("produces report with required section headers", () => {
    const tmp = fs.mkdtempSync(path.join(os.tmpdir(), "abs286-report-"));
    const baseDir = path.join(tmp, "opus-run");
    const candDir = path.join(tmp, "sonnet-run");

    // Identical token counts for 2 cases so pricing difference is the only variable
    const cases = [
      { id: "TC-001", input: 10_000, output: 1_000, iters: 3 },
      { id: "TC-002", input: 8_000, output: 800, iters: 2 },
    ];
    writeSyntheticRun(baseDir, "claude-opus-4-5", cases);
    writeSyntheticRun(candDir, "claude-sonnet-4-6", cases);

    const { stdout, status } = runScript("--baseline", baseDir, "--candidate", candDir);
    expect(status).toBe(0);

    expect(stdout).toContain("# A/B Model Comparison Report");
    expect(stdout).toContain("## 1. Aggregate Cost Summary");
    expect(stdout).toContain("## 2. Tool Loop Metrics");
    expect(stdout).toContain("## 3. Per-Case Comparison");
    expect(stdout).toContain("## 4. Quality Comparison");
    expect(stdout).toContain("## 5. Verdict");

    fs.rmSync(tmp, { recursive: true, force: true });
  });

  test("cost ratio is ~5× cheaper for Sonnet vs Opus on identical token counts", () => {
    const tmp = fs.mkdtempSync(path.join(os.tmpdir(), "abs286-ratio-"));
    const baseDir = path.join(tmp, "opus-run");
    const candDir = path.join(tmp, "sonnet-run");

    const cases = [
      { id: "TC-001", input: 100_000, output: 10_000, iters: 4 },
    ];
    writeSyntheticRun(baseDir, "claude-opus-4-5", cases);
    writeSyntheticRun(candDir, "claude-sonnet-4-6", cases);

    const { stdout, status } = runScript("--baseline", baseDir, "--candidate", candDir);
    expect(status).toBe(0);

    // Expect the cost-ratio line: Opus costs 5× more than Sonnet
    expect(stdout).toMatch(/5\.0× cheaper on candidate/);

    fs.rmSync(tmp, { recursive: true, force: true });
  });

  test("SWITCH TO SONNET verdict when quality data shows candidate quality holds", () => {
    const tmp = fs.mkdtempSync(path.join(os.tmpdir(), "abs286-switch-"));
    const baseDir = path.join(tmp, "opus-run");
    const candDir = path.join(tmp, "sonnet-run");

    const cases = [
      { id: "TC-001", input: 100_000, output: 10_000, iters: 3 },
    ];
    writeSyntheticRun(baseDir, "claude-opus-4-5", cases);
    writeSyntheticRun(candDir, "claude-sonnet-4-6", cases);

    // Provide quality data: both PASS, no hallucinations
    const makeGrade = (id: string, verdict: string) =>
      JSON.stringify({ id, grade: { verdict, keyword_rate: 0.9, citation_hallucinated: 0 } });
    fs.mkdirSync(path.join(baseDir, "verification"), { recursive: true });
    fs.mkdirSync(path.join(candDir, "verification"), { recursive: true });
    fs.writeFileSync(path.join(baseDir, "verification", "TC-001.verify.json"), makeGrade("TC-001", "PASS"));
    fs.writeFileSync(path.join(candDir, "verification", "TC-001.verify.json"), makeGrade("TC-001", "PASS"));

    const { stdout, status } = runScript("--baseline", baseDir, "--candidate", candDir);
    expect(status).toBe(0);
    expect(stdout).toContain("SWITCH TO SONNET");

    fs.rmSync(tmp, { recursive: true, force: true });
  });

  test("--output-md writes the report to the specified path", () => {
    const tmp = fs.mkdtempSync(path.join(os.tmpdir(), "abs286-outmd-"));
    const baseDir = path.join(tmp, "opus-run");
    const candDir = path.join(tmp, "sonnet-run");
    const outMd = path.join(tmp, "AB_COMPARISON.md");

    const cases = [
      { id: "TC-001", input: 5_000, output: 500, iters: 2 },
    ];
    writeSyntheticRun(baseDir, "claude-opus-4-5", cases);
    writeSyntheticRun(candDir, "claude-sonnet-4-6", cases);

    const { status } = runScript(
      "--baseline", baseDir,
      "--candidate", candDir,
      "--output-md", outMd,
    );
    expect(status).toBe(0);
    expect(fs.existsSync(outMd)).toBe(true);

    const content = fs.readFileSync(outMd, "utf-8");
    expect(content).toContain("# A/B Model Comparison Report");

    fs.rmSync(tmp, { recursive: true, force: true });
  });

  test("terminated_reason distribution appears in report", () => {
    const tmp = fs.mkdtempSync(path.join(os.tmpdir(), "abs286-term-"));
    const baseDir = path.join(tmp, "opus-run");
    const candDir = path.join(tmp, "sonnet-run");

    // Baseline has iteration_cap hits; candidate has normal completions
    fs.mkdirSync(baseDir, { recursive: true });
    fs.mkdirSync(candDir, { recursive: true });
    fs.writeFileSync(
      path.join(baseDir, "TC-001.json"),
      JSON.stringify(
        makeTranscript("TC-001", "claude-opus-4-5", 10_000, 1_000, 5, "iteration_cap"),
        null, 2,
      ),
    );
    fs.writeFileSync(
      path.join(candDir, "TC-001.json"),
      JSON.stringify(
        makeTranscript("TC-001", "claude-sonnet-4-6", 10_000, 1_000, 3, "stop"),
        null, 2,
      ),
    );

    const { stdout, status } = runScript("--baseline", baseDir, "--candidate", candDir);
    expect(status).toBe(0);
    expect(stdout).toContain("terminated_reason distribution");
    expect(stdout).toContain("iteration_cap");
    expect(stdout).toContain("stop");

    fs.rmSync(tmp, { recursive: true, force: true });
  });
});
