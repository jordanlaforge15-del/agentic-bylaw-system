// Functional: ABS-502 — the retrieval baseline cannot go stale in silence
//
// evals/retrieval/BASELINE.json was written once at ABS-486 and then went stale
// without anyone noticing. ABS-478 and ABS-488 both moved retrieval; the file
// still read Recall@10 = 0.1029 while dev was at 0.1618. Two things followed:
// ABS-494 argued its whole case ("+0.34, zero regressions") against a control
// that had already moved, and ABS-488's ranking inversion — container prose
// banking path weight on a child clause — shipped with nothing to catch it.
// ABS-492 found that inversion by accident, doing unrelated work.
//
// A stale baseline is worse than no baseline: it does not merely fail to catch
// a regression, it certifies one. So this spec puts the staleness check in the
// same offline gate that already runs the golden-case check (abs468), which is
// the gate a merge has to pass:
//
//   1. The committed baseline describes the committed retrieval code. This is
//      the assertion that fails on a retrieval-affecting merge that forgot to
//      re-record — the whole point of the ticket.
//   2. The check actually detects drift, rather than passing because it looked
//      at nothing. Asserted by handing it a baseline stamped against different
//      content and requiring a non-zero exit.
//   3. The failure message names the one command that fixes it. A gate whose
//      message does not say what to run gets worked around, not obeyed.
//   4. That command exists in the Makefile and points at the harness — so the
//      instruction in (3) cannot rot into a lie.
//
// Like abs468/abs486 this is offline: no stack, no database, no network. The
// Python-side twin, tests/scripts/test_check_retrieval_baseline.py, drives the
// normalisation and the acknowledgement paths against synthetic trees.

import { spawnSync } from "child_process";
import * as fs from "fs";
import * as os from "os";
import * as path from "path";
import { expect, test } from "@playwright/test";

const REPO_ROOT = path.resolve(__dirname, "../../../");
const SCRIPT = path.join(REPO_ROOT, "scripts", "check_retrieval_baseline.py");
const VENV_PYTHON = path.join(REPO_ROOT, ".venv", "bin", "python");
const BASELINE_FILE = path.join(REPO_ROOT, "evals", "retrieval", "BASELINE.json");
const README_FILE = path.join(REPO_ROOT, "evals", "retrieval", "README.md");
const MAKEFILE = path.join(REPO_ROOT, "Makefile");

const REGENERATE_COMMAND = "make eval-retrieval-baseline";

type Verdict = {
  verdict: "fresh" | "acknowledged" | "stale";
  reason: string;
  added: string[];
  removed: string[];
  modified: string[];
  recorded_commit: string | null;
  regenerate_command: string;
};

function runCheck(args: string[] = []): { status: number; stdout: string; stderr: string } {
  const run = spawnSync(VENV_PYTHON, [SCRIPT, ...args], {
    cwd: REPO_ROOT,
    encoding: "utf-8",
  });
  return { status: run.status ?? -1, stdout: run.stdout ?? "", stderr: run.stderr ?? "" };
}

test.describe("ABS-502 retrieval baseline freshness gate", () => {
  test("the committed baseline describes the committed retrieval code", () => {
    const run = runCheck(["--json"]);
    const verdict = JSON.parse(run.stdout) as Verdict;

    expect(
      verdict.verdict,
      `evals/retrieval/BASELINE.json is ${verdict.verdict}: ${verdict.reason}\n` +
        `Drifted: ${[...verdict.added, ...verdict.removed, ...verdict.modified].join(", ")}\n` +
        `Fix: ${REGENERATE_COMMAND}`,
    ).not.toBe("stale");
    expect(run.status).toBe(0);
    expect(verdict.regenerate_command).toBe(REGENERATE_COMMAND);
  });

  test("the check detects drift rather than passing on nothing", () => {
    // A baseline stamped against content that is not in the tree. If the check
    // were vacuous — reading the wrong key, comparing a value to itself — this
    // is the assertion that catches it.
    const dir = fs.mkdtempSync(path.join(os.tmpdir(), "abs502-"));
    const baseline = JSON.parse(fs.readFileSync(BASELINE_FILE, "utf-8"));
    const files = { ...baseline.retrieval_fingerprint.files };
    const [first] = Object.keys(files).sort();
    files[first] = "sha256:" + "0".repeat(64);
    baseline.retrieval_fingerprint = {
      ...baseline.retrieval_fingerprint,
      digest: "sha256:" + "0".repeat(64),
      files,
      acknowledged_drift: null,
    };
    const stalePath = path.join(dir, "STALE-BASELINE.json");
    fs.writeFileSync(stalePath, JSON.stringify(baseline));

    const run = runCheck(["--baseline", stalePath]);
    expect(run.status).toBe(1);
    expect(run.stderr).toContain("STALE");
    expect(run.stderr).toContain(first);
  });

  test("the failure message says exactly what to run", () => {
    const dir = fs.mkdtempSync(path.join(os.tmpdir(), "abs502-msg-"));
    const noFingerprint = path.join(dir, "BASELINE.json");
    fs.writeFileSync(noFingerprint, JSON.stringify({ recall_at_k: 0.1029 }));

    const run = runCheck(["--baseline", noFingerprint]);
    expect(run.status).toBe(1);
    expect(run.stderr).toContain(REGENERATE_COMMAND);
  });

  test("the regeneration command exists and is documented", () => {
    const makefile = fs.readFileSync(MAKEFILE, "utf-8");
    expect(makefile).toContain("eval-retrieval-baseline:");
    expect(makefile).toContain("scripts/eval_retrieval_recall.py");
    expect(makefile).toContain("check-retrieval-baseline:");
    // The README is where a human looks first; the command has to be named
    // there, not only in a Makefile nobody greps.
    expect(fs.readFileSync(README_FILE, "utf-8")).toContain(REGENERATE_COMMAND);
  });

  test("the baseline records which retrieval code produced its numbers", () => {
    const baseline = JSON.parse(fs.readFileSync(BASELINE_FILE, "utf-8"));
    const fingerprint = baseline.retrieval_fingerprint;
    expect(fingerprint, "BASELINE.json carries no retrieval_fingerprint").toBeTruthy();
    expect(fingerprint.digest).toMatch(/^sha256:[0-9a-f]{64}$/);
    expect(Object.keys(fingerprint.files).length).toBeGreaterThan(5);
    // The files that actually moved the number across ABS-488/492/500 have to
    // be among the watched set, or the gate watches the wrong thing.
    for (const watched of [
      "mcp/bylaw_retrieval/retrieval/service.py",
      "mcp/bylaw_retrieval/retrieval/context.py",
      "mcp/bylaw_retrieval/retrieval/binding.py",
      "src/layer1/pipeline/hierarchy.py",
      "evals/retrieval/queries.json",
    ]) {
      expect(fingerprint.files[watched], `${watched} is not watched`).toMatch(/^sha256:/);
    }
  });
});
