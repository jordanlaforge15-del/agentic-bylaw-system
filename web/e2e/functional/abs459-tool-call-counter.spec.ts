// ABS-459: run_test_prompts.py reported `tool_calls: 0` for every case
//
// The parser harvested tool calls from the SSE *content* stream, watching
// content_block_start for `type: "tool_use"`. But advisor/chat/session.py
// synthesises that stream from the tool loop's FINAL response, which by
// construction holds no tool_use blocks — the loop has already settled to
// end_turn before the first SSE byte. So the harvest was empty on every
// backend, and every transcript claimed the advisor called no tools while
// its own tool_loop_metrics recorded many.
//
// This issue's deliverable is a script fix plus the artifacts it produces,
// not UI — so coverage is a self-consistency contract over committed run
// artifacts, the shape established by abs306-eval-suite-validation.spec.ts
// and abs458-claude-code-eval-artifacts.spec.ts.
//
// Validates:
//   1. The invariant, over every committed transcript: a turn whose
//      tool_loop_metrics reports dispatched calls must not report an empty
//      tool_calls array. This is the regression itself.
//   2. SUMMARY.json's per-case tool_calls count equals the number of entries
//      summed across that case's turns — the number a human reads.
//   3. The ABS-459 evidence run is committed and still carries the bug's
//      signature, so the fixture the unit tests re-derive from cannot
//      silently drift.

import * as fs from "fs";
import * as path from "path";
import { test, expect } from "../fixtures/test-env";

const REPO_ROOT = path.resolve(__dirname, "../../../");
const RUNS_DIR = path.join(REPO_ROOT, "evals", "runs");
// The run that exposed the bug: 16 dispatched calls, all reported as zero.
const EVIDENCE_DIR = path.join(RUNS_DIR, "20260811T113204Z");

interface ToolCallMetric {
  name: string;
  is_error?: boolean;
  latency_ms?: number;
}

interface IterationMetric {
  iteration: number;
  tool_call_count?: number;
}

interface ToolLoopMetrics {
  iterations?: number;
  tool_calls?: ToolCallMetric[];
  per_iteration?: IterationMetric[];
}

interface Turn {
  turn: number;
  tool_calls?: unknown[];
  tool_loop_metrics?: ToolLoopMetrics | null;
}

interface Transcript {
  id: string;
  turns: Turn[];
}

interface SummaryRow {
  id: string;
  tool_calls?: number;
}

/** Calls the loop actually dispatched, per tool_loop_metrics. */
function dispatchedCalls(metrics: ToolLoopMetrics | null | undefined): number {
  if (!metrics) return 0;
  if (Array.isArray(metrics.tool_calls)) return metrics.tool_calls.length;
  return (metrics.per_iteration ?? []).reduce(
    (acc, it) => acc + (it.tool_call_count ?? 0),
    0,
  );
}

function transcriptsIn(dir: string): { file: string; doc: Transcript }[] {
  if (!fs.existsSync(dir)) return [];
  return fs
    .readdirSync(dir)
    .filter((f) => /^TC-\d{3}\.json$/.test(f))
    .map((f) => ({
      file: path.join(dir, f),
      doc: JSON.parse(fs.readFileSync(path.join(dir, f), "utf-8")) as Transcript,
    }));
}

/** Every committed run directory holding TC-NNN transcripts. */
function allRunDirs(): string[] {
  if (!fs.existsSync(RUNS_DIR)) return [];
  const out: string[] = [];
  const walk = (dir: string, depth: number) => {
    if (depth > 2) return;
    for (const entry of fs.readdirSync(dir, { withFileTypes: true })) {
      if (!entry.isDirectory()) continue;
      const full = path.join(dir, entry.name);
      if (transcriptsIn(full).length > 0) out.push(full);
      else walk(full, depth + 1);
    }
  };
  walk(RUNS_DIR, 0);
  return out;
}

test.describe("ABS-459 — tool_calls reflects what the loop dispatched", () => {
  test("the evidence run is committed and still shows the bug's signature", () => {
    const transcripts = transcriptsIn(EVIDENCE_DIR);
    expect(
      transcripts.length,
      `ABS-459 evidence run missing at ${EVIDENCE_DIR}; the unit tests re-derive from it`,
    ).toBeGreaterThan(0);

    const tc001 = transcripts.find((t) => t.doc.id === "TC-001");
    expect(tc001).toBeDefined();

    // Captured under the buggy parser: metrics say 16, tool_calls say none.
    const dispatched = tc001!.doc.turns.reduce(
      (acc, t) => acc + dispatchedCalls(t.tool_loop_metrics),
      0,
    );
    expect(dispatched).toBe(16);
    for (const turn of tc001!.doc.turns) {
      expect(
        turn.tool_calls,
        `evidence transcript turn ${turn.turn} should still carry the empty array it was captured with`,
      ).toEqual([]);
    }
  });

  test("no transcript claims zero tool calls while its metrics report some", () => {
    const runDirs = allRunDirs();
    expect(runDirs.length).toBeGreaterThan(0);

    const violations: string[] = [];

    for (const dir of runDirs) {
      // The ABS-459 evidence run is the captured bug itself — exempt.
      if (path.resolve(dir) === path.resolve(EVIDENCE_DIR)) continue;

      for (const { file, doc } of transcriptsIn(dir)) {
        for (const turn of doc.turns ?? []) {
          const dispatched = dispatchedCalls(turn.tool_loop_metrics);
          const recorded = (turn.tool_calls ?? []).length;
          if (dispatched > 0 && recorded === 0) {
            violations.push(
              `${path.relative(REPO_ROOT, file)} turn ${turn.turn}: ` +
                `metrics dispatched ${dispatched}, tool_calls recorded 0`,
            );
          }
        }
      }
    }

    expect(
      violations,
      `Transcripts under-report dispatched tool calls:\n  ${violations.join("\n  ")}`,
    ).toEqual([]);
  });

  test("SUMMARY.json tool_calls equals the sum across that case's turns", () => {
    const violations: string[] = [];

    for (const dir of allRunDirs()) {
      const summaryPath = path.join(dir, "SUMMARY.json");
      if (!fs.existsSync(summaryPath)) continue;

      const summary = JSON.parse(
        fs.readFileSync(summaryPath, "utf-8"),
      ) as SummaryRow[];
      if (!Array.isArray(summary)) continue;

      const byId = new Map(
        transcriptsIn(dir).map(({ doc }) => [doc.id, doc]),
      );

      for (const row of summary) {
        if (typeof row.tool_calls !== "number") continue;
        const doc = byId.get(row.id);
        if (!doc) continue;

        const summed = (doc.turns ?? []).reduce(
          (acc, t) => acc + (t.tool_calls ?? []).length,
          0,
        );
        if (row.tool_calls !== summed) {
          violations.push(
            `${path.relative(REPO_ROOT, summaryPath)} ${row.id}: ` +
              `summary says ${row.tool_calls}, transcript turns sum to ${summed}`,
          );
        }
      }
    }

    expect(
      violations,
      `SUMMARY.json disagrees with its own transcripts:\n  ${violations.join("\n  ")}`,
    ).toEqual([]);
  });
});
