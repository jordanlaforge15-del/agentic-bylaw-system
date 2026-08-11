// Functional: ABS-457 — the live-validation script's spend guards.
//
// scripts/validate_claude_code_gateway.py is the one thing in this repo that
// shells out to the real `claude` binary and bills real turns. It has no
// web-UI surface, so — like abs286-compare-ab-runs.spec.ts and
// abs203-test-prompts.spec.ts — it is driven here with spawnSync.
//
// tests/scripts/test_validate_claude_code_gateway.py already pins the verdict
// logic and calls `main()` in-process with a hand-built env dict. That is the
// right shape for the branch logic and structurally blind to the two things
// that decide whether an invocation can cost money — the same gap
// abs456-llm-registry-provider.spec.ts opened for the registry:
//
//  (a) WHETHER THE GUARDS FIRE ON A REAL os.environ, IN THE RIGHT ORDER. The
//      failure this backend exists to prevent is `claude -p` running with
//      ANTHROPIC_API_KEY present, which bills API rates instead of the
//      operator's subscription (anthropics/claude-code#43333). A monkeypatched
//      dict proves the `if`; only a spawned interpreter proves the invocation.
//      The refusal must also beat the opt-in gate, so that the dangerous
//      combination — key set AND opt-in set — is refused rather than run.
//
//  (b) WHETHER THE MODULE STILL IMPORTS AT ALL. The script reaches into
//      ClaudeCodeGateway and the ABS-454 translation layer. Every case below
//      executes the module top to bottom in a fresh interpreter, so an import
//      that rots (a renamed symbol, an extra that slipped back under [dev])
//      is a red spec here rather than a discovery months later, mid-run,
//      after the first billed turn.
//
// Every case runs with PATH pointing at an empty directory, so `claude` cannot
// be resolved even if a guard were to fail open. No CLI turn is billed by this
// spec, and no `claude` binary needs to exist in the e2e container.

import { spawnSync } from "child_process";
import * as fs from "fs";
import * as os from "os";
import * as path from "path";
import { expect, test } from "../fixtures/test-env";

const REPO_ROOT = path.resolve(__dirname, "../../../");
const SCRIPT = path.join(
  REPO_ROOT,
  "scripts",
  "validate_claude_code_gateway.py",
);
const VENV_PYTHON = path.join(REPO_ROOT, ".venv", "bin", "python");

/** Absolute path to a fresh temp dir, cleaned up by the OS. */
function tempDir(prefix: string): string {
  return fs.mkdtempSync(path.join(os.tmpdir(), prefix));
}

type Run = { stdout: string; stderr: string; status: number };

/**
 * Spawn the script in a fresh interpreter.
 *
 * The child inherits the runner's environment, minus the two variables that
 * decide the outcome: each case states its own, so a stray
 * ANTHROPIC_API_KEY in the developer's shell cannot flip a result. PATH is
 * replaced with an empty directory, so `shutil.which("claude")` must miss.
 */
function runScript(
  env: Record<string, string>,
  args: string[] = [],
): Run & { reportRoot: string } {
  const reportRoot = tempDir("abs457-report-");
  const emptyPath = tempDir("abs457-nopath-");

  const childEnv: NodeJS.ProcessEnv = { ...process.env, PATH: emptyPath, ...env };
  if (!("ANTHROPIC_API_KEY" in env)) delete childEnv.ANTHROPIC_API_KEY;
  if (!("ABS_RUN_LIVE_CLAUDE_CODE" in env)) delete childEnv.ABS_RUN_LIVE_CLAUDE_CODE;

  const result = spawnSync(
    VENV_PYTHON,
    [SCRIPT, "--report-root", reportRoot, ...args],
    { cwd: REPO_ROOT, encoding: "utf-8", env: childEnv },
  );
  return {
    stdout: result.stdout ?? "",
    stderr: result.stderr ?? "",
    status: result.status ?? -1,
    reportRoot,
  };
}

/** The script promises "No report written" on every non-running path. */
function expectNoReport(reportRoot: string): void {
  const written = fs.existsSync(reportRoot) ? fs.readdirSync(reportRoot) : [];
  expect(written, `report root should be empty, found: ${written.join(", ")}`)
    .toHaveLength(0);
}

test.describe("ABS-457 live-validation guards (real interpreter)", () => {
  test("(a) ANTHROPIC_API_KEY set → exit 2, refusal, no report", () => {
    const run = runScript({ ANTHROPIC_API_KEY: "sk-ant-not-a-real-key" });

    expect(run.status, `stdout: ${run.stdout}\nstderr: ${run.stderr}`).toBe(2);
    expect(run.stderr).toContain("REFUSING TO RUN");
    expect(run.stderr).toContain("ANTHROPIC_API_KEY");
    expectNoReport(run.reportRoot);
  });

  test("(b) key set AND opt-in set → still refused (refusal precedes the gate)", () => {
    // The dangerous combination. If the opt-in gate were checked first, an
    // operator who exported both would silently bill at API rates.
    const run = runScript({
      ANTHROPIC_API_KEY: "sk-ant-not-a-real-key",
      ABS_RUN_LIVE_CLAUDE_CODE: "1",
    });

    expect(run.status, `stdout: ${run.stdout}\nstderr: ${run.stderr}`).toBe(2);
    expect(run.stderr).toContain("REFUSING TO RUN");
    expect(run.stdout).not.toContain("live validation");
    expectNoReport(run.reportRoot);
  });

  test("(c) no opt-in → exit 0, SKIPPED, no report", () => {
    // This is the path `make test` and any unsuspecting caller takes. It must
    // be free and silent about failure.
    const run = runScript({});

    expect(run.status, `stdout: ${run.stdout}\nstderr: ${run.stderr}`).toBe(0);
    expect(run.stdout).toContain("SKIPPED");
    expect(run.stdout).toContain("ABS_RUN_LIVE_CLAUDE_CODE");
    expect(run.stdout).toContain("No report written");
    expectNoReport(run.reportRoot);
  });

  test("(d) opt-in must be the literal '1' — 'true' does not opt in", () => {
    // Truthy-looking values are a common way to arm something by accident.
    const run = runScript({ ABS_RUN_LIVE_CLAUDE_CODE: "true" });

    expect(run.status, `stdout: ${run.stdout}\nstderr: ${run.stderr}`).toBe(0);
    expect(run.stdout).toContain("SKIPPED");
    expectNoReport(run.reportRoot);
  });

  test("(e) empty ANTHROPIC_API_KEY is not a key — falls through to the gate", () => {
    // `env -u` is the documented invocation, but shells and CI runners often
    // export the variable empty instead. Empty must not be mistaken for a set
    // key (that would make the refusal unbypassable), nor for permission to
    // run (the opt-in gate still has to stop it).
    const run = runScript({ ANTHROPIC_API_KEY: "" });

    expect(run.status, `stdout: ${run.stdout}\nstderr: ${run.stderr}`).toBe(0);
    expect(run.stdout).toContain("SKIPPED");
    expectNoReport(run.reportRoot);
  });

  test("(f) opted in but no `claude` on PATH → exit 2 before any turn", () => {
    // The last guard between an opted-in run and a billed turn. It must fail
    // closed: no report directory, no partial results, a message that names
    // the missing binary.
    const run = runScript({ ABS_RUN_LIVE_CLAUDE_CODE: "1" });

    expect(run.status, `stdout: ${run.stdout}\nstderr: ${run.stderr}`).toBe(2);
    expect(run.stderr).toContain("PRECONDITION FAILED");
    expect(run.stderr).toContain("claude");
    expectNoReport(run.reportRoot);
  });

  test("(g) --help exits 0 — the module imports cleanly in a fresh interpreter", () => {
    // Nothing here asserts help text beyond the flags; the value is that
    // argparse only reaches --help after every top-level import has resolved,
    // including ClaudeCodeGateway and the ABS-454 translation layer.
    const run = runScript({}, ["--help"]);

    expect(run.status, `stdout: ${run.stdout}\nstderr: ${run.stderr}`).toBe(0);
    expect(run.stdout).toContain("--report-root");
    expect(run.stdout).toContain("--cli-path");
    expect(run.stderr).not.toContain("Traceback");
  });
});
