// Functional: ABS-485 — the golden-case deploy gate is enforced, not just written down
//
// ABS-468 established the rule in two places: the `gate` block in
// evals/golden/golden_cases.json ("a production deploy requires every entry
// here to be attested and to grade GOLDEN_PASS") and evals/golden/README.md.
// Nothing ran it. No CI job referenced the golden subset, and neither the
// deploy-bylaw nor the test-and-deploy-bylaw skill invoked a grader — which is
// how a known-wrong answer reached production
// (docs/data-gaps/abs461-production-impact.md).
//
// This spec pins the properties that make the difference between a rule and a
// gate:
//
//   1. The committed corpus demonstrably HOLDS promotion today, and the exit
//      code says so — the state the ticket calls the designed behaviour.
//   2. A hold reads differently from a failure. Both stop a promotion; the
//      operator's next move is opposite, so the output has to distinguish them.
//   3. "Could not evaluate" cannot masquerade as a pass.
//   4. A green grade cannot be inherited by an edited golden file.
//   5. All three enforcement points actually reference the script, and CI's is
//      hard at the dev → main boundary rather than decorative.
//   6. Nobody has authored an attestation to make any of this green.
//
// No product behaviour changed in ABS-485, so like abs468/abs132 this drives
// the CLI and reads the repo — no stack, no database, no API spend.

import { spawnSync } from "child_process";
import * as crypto from "crypto";
import * as fs from "fs";
import * as os from "os";
import * as path from "path";
// Imported from @playwright/test rather than ../fixtures/test-env on purpose:
// every assertion below is a CLI spawn or a file read, so this spec has no
// business failing because the stack's auth seeding hiccuped. The shared
// fixture's auto authedContext would make a repo-invariant check depend on a
// booted web server.
import { expect, test } from "@playwright/test";

const REPO_ROOT = path.resolve(__dirname, "../../../");
const GATE = path.join(REPO_ROOT, "scripts", "check_deploy_gate.py");
const VENV_PYTHON = path.join(REPO_ROOT, ".venv", "bin", "python");
const GOLDEN_FILE = path.join(REPO_ROOT, "evals", "golden", "golden_cases.json");
const CI_WORKFLOW = path.join(REPO_ROOT, ".github", "workflows", "ci.yml");
const TEST_AND_DEPLOY_SKILL = path.join(
  REPO_ROOT,
  ".claude",
  "skills",
  "test-and-deploy-bylaw",
  "SKILL.md",
);
const DEPLOY_SKILL = path.join(REPO_ROOT, ".claude", "skills", "deploy-bylaw", "SKILL.md");

// The script's own contract. 2 is deliberately not 1: a pipeline that treats
// "the golden file is missing" as "the gate is closed" is fine; one that treats
// it as "the gate is open" ships a wrong answer.
const EXIT_OPEN = 0;
const EXIT_HELD = 1;
const EXIT_USAGE = 2;

type GateDecision = {
  open: boolean;
  reason_code: string;
  explanation: string;
  next_step: string;
  golden_file_sha256: string;
  attestation: { total: number; attested: number; unattested: string[] };
  graded_run?: { run_dir: string };
};

function runGate(args: string[] = []): { status: number; stdout: string; stderr: string } {
  const run = spawnSync(VENV_PYTHON, [GATE, ...args], {
    cwd: REPO_ROOT,
    encoding: "utf-8",
  });
  return { status: run.status ?? -1, stdout: run.stdout ?? "", stderr: run.stderr ?? "" };
}

function tmpDir(name: string): string {
  return fs.mkdtempSync(path.join(os.tmpdir(), `abs485-${name}-`));
}

/** A synthetic golden entry a human has (fictionally) attested. */
function attestedCase(caseId = "TC-001"): Record<string, unknown> {
  return {
    case_id: caseId,
    zone: "HR-1",
    liability: "low",
    answer_shape: "determinate",
    selection_rationale: "synthetic fixture for the gate's own tests",
    question_for_reviewer: "What side setback governs a lot in this zone?",
    attestation: {
      status: "attested",
      attested_by: { name: "A. Reviewer", credential: "MCIP, LPP" },
      attested_on: "2026-08-20",
      method: "read the by-law",
      correct_answer: "2.5 m side setback applies.",
      governing_provisions: [
        { reference: "Section 198", holding: "side setback standards" },
      ],
      must_state: [
        { id: "side-setback", description: "gives 2.5 m", any_of: ["2.5 m"] },
      ],
      must_not_state: [],
    },
  };
}

function writeGolden(dir: string, cases: unknown[]): string {
  const p = path.join(dir, "golden.json");
  fs.writeFileSync(p, JSON.stringify({ schema_version: 1, cases }, null, 2));
  return p;
}

function sha256OfFile(p: string): string {
  return crypto.createHash("sha256").update(fs.readFileSync(p)).digest("hex");
}

/** A run directory carrying only what the gate reads: GOLDEN_SUMMARY.json. */
function writeGradedRun(
  runsRoot: string,
  stamp: string,
  opts: { digest: string | null; verdicts: Record<string, string> },
): void {
  const verification = path.join(runsRoot, stamp, "verification");
  fs.mkdirSync(verification, { recursive: true });
  const failing = Object.entries(opts.verdicts)
    .filter(([, v]) => v !== "GOLDEN_PASS")
    .map(([cid]) => cid);
  const summary: Record<string, unknown> = {
    evidence_tier: "human_validated",
    golden_file: "golden.json",
    run_dir: path.join(runsRoot, stamp),
    gate: {
      gates: "production_deploy",
      open: failing.length === 0,
      blockers: failing.length ? [`not passing: ${failing.join(", ")}`] : [],
      counts: { GOLDEN_PASS: Object.keys(opts.verdicts).length - failing.length },
    },
    cases: Object.entries(opts.verdicts).map(([case_id, verdict]) => ({
      case_id,
      verdict,
      reasons: verdict === "GOLDEN_PASS" ? [] : ["did not cite Section 198"],
    })),
  };
  if (opts.digest !== null) summary.golden_file_sha256 = opts.digest;
  fs.writeFileSync(
    path.join(verification, "GOLDEN_SUMMARY.json"),
    JSON.stringify(summary, null, 2),
  );
}

// ─── 1. The committed corpus holds promotion today ───────────────────────────

test.describe("ABS-485 the committed corpus holds promotion", () => {
  test("the gate exits non-zero and says HELD, not FAILED", () => {
    // The DoD's central evidence: run the skill's verify step against the real
    // corpus and watch it refuse. If this ever goes green without a reviewer's
    // name landing in the golden file, something has backfilled an attestation.
    const run = runGate();
    expect(run.status, run.stdout + run.stderr).toBe(EXIT_HELD);
    expect(run.stdout).toContain("GATE: HELD (unattested)");
    expect(run.stdout).toContain("DO NOT PROMOTE OR DEPLOY");
    expect(run.stdout).toContain("HOLD, not a FAILURE");
    expect(run.stdout).toContain("attested    : 0/6 entries");
  });

  test("the hold points at the attestation procedure and forbids authoring one", () => {
    // This output is the only thing standing between an agent reading "gate
    // held" and an agent helpfully filling in the blanks.
    const { stdout } = runGate();
    expect(stdout).toContain("evals/golden/README.md");
    expect(stdout).toContain("Filling in an attestation");
    expect(stdout).toContain("NOT a model");
    expect(stdout.toLowerCase()).toContain("qualified");
  });

  test("--json carries the reason code a pipeline branches on", () => {
    const run = runGate(["--json"]);
    expect(run.status).toBe(EXIT_HELD);
    const decision = JSON.parse(run.stdout) as GateDecision;
    expect(decision.open).toBe(false);
    expect(decision.reason_code).toBe("unattested");
    expect(decision.attestation.total).toBe(6);
    expect(decision.attestation.attested).toBe(0);
    expect(decision.attestation.unattested).toHaveLength(6);
    // The digest is what a later grade must match to be trusted.
    expect(decision.golden_file_sha256).toBe(sha256OfFile(GOLDEN_FILE));
  });

  test("no attestation has been authored to make any of this pass", () => {
    // ABS-468 pins the same property; it is repeated here because ABS-485 is
    // precisely the change that creates a motive to violate it.
    const { cases } = JSON.parse(fs.readFileSync(GOLDEN_FILE, "utf-8")) as {
      cases: Array<{
        case_id: string;
        attestation: { status: string; correct_answer: string | null };
      }>;
    };
    for (const c of cases) {
      expect(c.attestation.status, c.case_id).toBe("unattested");
      expect(c.attestation.correct_answer, c.case_id).toBeNull();
    }
  });
});

// ─── 2. Exit codes are the contract ──────────────────────────────────────────

test.describe("ABS-485 exit codes", () => {
  test("attested and graded-passing exits 0", () => {
    const dir = tmpDir("open");
    const runsRoot = path.join(dir, "runs");
    fs.mkdirSync(runsRoot);
    const golden = writeGolden(dir, [attestedCase()]);
    writeGradedRun(runsRoot, "20260828T000000Z", {
      digest: sha256OfFile(golden),
      verdicts: { "TC-001": "GOLDEN_PASS" },
    });
    const run = runGate(["--golden", golden, "--runs-root", runsRoot]);
    expect(run.status, run.stdout + run.stderr).toBe(EXIT_OPEN);
    expect(run.stdout).toContain("GATE: OPEN");
  });

  test("a graded failure exits non-zero and refuses the tempting fix", () => {
    const dir = tmpDir("failing");
    const runsRoot = path.join(dir, "runs");
    fs.mkdirSync(runsRoot);
    const golden = writeGolden(dir, [attestedCase()]);
    writeGradedRun(runsRoot, "20260828T000000Z", {
      digest: sha256OfFile(golden),
      verdicts: { "TC-001": "GOLDEN_FAIL" },
    });
    const run = runGate(["--golden", golden, "--runs-root", runsRoot]);
    expect(run.status).toBe(EXIT_HELD);
    expect(run.stdout).toContain("GATE: HELD (graded_failing)");
    expect(run.stdout).toContain("FAILURE, not a hold");
    // A human said X, the advisor said Y. Fixing the human's answer is the one
    // move that destroys the artifact, so the output names it.
    expect(run.stdout.toLowerCase()).toContain(
      "do not edit the attestation to match",
    );
  });

  test("a missing golden file exits 2, never 0", () => {
    const dir = tmpDir("missing");
    const run = runGate(["--golden", path.join(dir, "nope.json")]);
    expect(run.status).toBe(EXIT_USAGE);
    expect(run.stderr).toContain("not a passing gate");
    expect(run.stdout).not.toContain("GATE: OPEN");
  });

  test("a malformed golden file is a refusal, not a verdict", () => {
    const dir = tmpDir("malformed");
    const broken = attestedCase() as Record<string, unknown>;
    broken.answer_shape = "not-a-shape";
    const run = runGate(["--golden", writeGolden(dir, [broken])]);
    expect(run.status).toBe(EXIT_USAGE);
    expect(run.stderr).toContain("answer_shape");
    expect(run.stdout).not.toContain("GATE: OPEN");
  });
});

// ─── 3. A green grade cannot be inherited by an edited golden file ───────────

test.describe("ABS-485 a grade belongs to the file it graded", () => {
  test("editing an attestation after a green run re-closes the gate", () => {
    // attest → grade → green → edit an attestation → promote. Without the
    // digest this reads as gated: the stale summary still says the gate opened.
    // It opened for a file that no longer exists.
    const dir = tmpDir("digest");
    const runsRoot = path.join(dir, "runs");
    fs.mkdirSync(runsRoot);
    const golden = writeGolden(dir, [attestedCase()]);
    writeGradedRun(runsRoot, "20260828T000000Z", {
      digest: sha256OfFile(golden),
      verdicts: { "TC-001": "GOLDEN_PASS" },
    });
    expect(runGate(["--golden", golden, "--runs-root", runsRoot]).status).toBe(EXIT_OPEN);

    const edited = JSON.parse(fs.readFileSync(golden, "utf-8"));
    edited.cases[0].attestation.correct_answer = "Actually 3.0 m applies.";
    fs.writeFileSync(golden, JSON.stringify(edited, null, 2));

    const run = runGate(["--golden", golden, "--runs-root", runsRoot, "--json"]);
    expect(run.status).toBe(EXIT_HELD);
    const decision = JSON.parse(run.stdout) as GateDecision;
    expect(decision.reason_code).toBe("no_graded_run");
    expect(decision.explanation).toContain("different golden file");
  });

  test("an attestation nothing has graded does not open the gate", () => {
    const dir = tmpDir("ungraded");
    const runsRoot = path.join(dir, "runs");
    fs.mkdirSync(runsRoot);
    const run = runGate([
      "--golden",
      writeGolden(dir, [attestedCase()]),
      "--runs-root",
      runsRoot,
      "--json",
    ]);
    expect(run.status).toBe(EXIT_HELD);
    const decision = JSON.parse(run.stdout) as GateDecision;
    expect(decision.reason_code).toBe("no_graded_run");
    expect(decision.next_step).toContain("scripts/verify_run.py");
  });
});

// ─── 4. The three enforcement points exist ───────────────────────────────────

test.describe("ABS-485 the gate is wired into the paths that ship", () => {
  test("CI runs it and blocks both prod image builds on it", () => {
    const ci = fs.readFileSync(CI_WORKFLOW, "utf-8");
    expect(ci).toContain("golden-gate:");
    expect(ci).toContain("scripts/check_deploy_gate.py");
    // `needs` is the load-bearing part: without it the job is a bystander that
    // goes red while the image it was supposed to stop is already in GHCR.
    const needs = ci.match(/needs: \[[^\]]*\]/g) ?? [];
    expect(needs.length).toBeGreaterThanOrEqual(2);
    for (const n of needs) expect(n).toContain("golden-gate");
    // …and it is hard at the dev → main boundary, not merely advisory.
    expect(ci).toContain("refs/heads/main");
    expect(ci).toContain("github.base_ref");
  });

  test("test-and-deploy-bylaw runs it before promoting", () => {
    const skill = fs.readFileSync(TEST_AND_DEPLOY_SKILL, "utf-8");
    expect(skill).toContain("scripts/check_deploy_gate.py");
    // Before 7.1 inspects the diff, not after the merge.
    expect(skill.indexOf("### 7.0")).toBeGreaterThan(0);
    expect(skill.indexOf("### 7.0")).toBeLessThan(skill.indexOf("### 7.1"));
    expect(skill).toContain("evals/golden/README.md");
    expect(skill).toContain("unattested");
    expect(skill).toContain("graded_failing");
  });

  test("deploy-bylaw checks it as a precondition for the direct path", () => {
    const skill = fs.readFileSync(DEPLOY_SKILL, "utf-8");
    expect(skill).toContain("scripts/check_deploy_gate.py");
    // It must land in Preconditions — after Step 3 has built and pushed an
    // image, holding the gate is too late to matter.
    const at = skill.indexOf("scripts/check_deploy_gate.py");
    expect(at).toBeGreaterThan(skill.indexOf("## Preconditions"));
    expect(at).toBeLessThan(skill.indexOf("## Step 1"));
  });

  test("every enforcement point forbids authoring an attestation", () => {
    // The gate creates the motive: a held gate is inconvenient and the fix
    // looks like six paragraphs of typing. Each place that can halt a release
    // has to say why that fix is the one thing not to do.
    for (const p of [TEST_AND_DEPLOY_SKILL, DEPLOY_SKILL, CI_WORKFLOW]) {
      const text = fs.readFileSync(p, "utf-8").toLowerCase();
      expect(text, `${path.basename(path.dirname(p))} must forbid it`).toContain(
        "attestation",
      );
      expect(
        /never author|do not author|not author|do not write the attestations/.test(text),
        `${p} must explicitly forbid authoring an attestation`,
      ).toBe(true);
    }
  });
});
