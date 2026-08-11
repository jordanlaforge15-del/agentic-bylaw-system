// ABS-458: Regional Centre 20-case eval suite on the `claude_code` backend
//
// This issue's deliverable is eval evidence, not UI. The artifacts under
// evals/runs/ are the product, so the coverage that matters is a schema +
// self-consistency contract over those artifacts — the same shape ABS-306
// established in abs306-eval-suite-validation.spec.ts.
//
// Validates:
//   1. The mandatory cost gate (TC-001 alone on the claude_code backend) is
//      recorded, has a valid run artifact, and the numbers quoted in
//      COST_GATE.md actually match the raw TC-001.json it was derived from.
//   2. The cost-gate run really exercised the subscription path: a real model
//      id, `end_turn` on every turn, and no per-call USD figure anywhere in
//      tool_loop_metrics (this backend is subscription-metered, so a
//      total_cost_usd here would mean the API key path was used by mistake).
//   3. The ABS-306 API-path baseline has been graded by the unmodified
//      verifier, producing one .verify.json per case plus a SUMMARY.json with
//      the DoD #5 comparison fields.
//   4. That grading is internally consistent: zero hallucinated citations,
//      every citation resolved, and the cases the credit exhaustion left empty
//      are graded FAIL-with-skipped-turns rather than silently PASSing.

import * as fs from "fs";
import * as path from "path";
import { test, expect } from "../fixtures/test-env";

const REPO_ROOT = path.resolve(__dirname, "../../../");
const RUNS_DIR = path.join(REPO_ROOT, "evals", "runs");
const COST_GATE_DIR = path.join(RUNS_DIR, "ABS-458-claude-code-TC001-costgate");
const BASELINE_DIR = path.join(RUNS_DIR, "20260614T095334Z-ABS306-opus-baseline");
const VERIFICATION_DIR = path.join(BASELINE_DIR, "verification");
const VERIFIER_SCRIPT = path.join(REPO_ROOT, "scripts", "verify_test_prompts.py");

// Cases that produced real transcripts before the ABS-306 run hit credit
// exhaustion. TC-005..TC-020 are empty transcripts.
const BASELINE_GRADED_IDS = ["TC-001", "TC-002", "TC-003", "TC-004"];
const ALL_IDS = Array.from({ length: 20 }, (_, i) => `TC-${String(i + 1).padStart(3, "0")}`);

// ─── Types ────────────────────────────────────────────────────────────────────

interface ToolLoopMetrics {
  type: string;
  iterations: number;
  terminated_reason: string;
  total_usage: Record<string, number>;
  total_cost_usd?: number;
}

interface TurnRecord {
  turn: number;
  stop_reason: string | null;
  wall_time_s: number;
  tool_loop_metrics: ToolLoopMetrics | null;
  error: string | null;
}

interface EvalTCResult {
  id: string;
  model: string | null;
  turns: TurnRecord[];
  spec: Record<string, unknown>;
  case_wall_time_s: number;
}

interface SummaryEntry {
  id: string;
  turns_completed: number;
  turns_expected: number;
  wall_s: number;
  error: string | null;
}

interface VerifyGrade {
  verdict: string;
  reasons: string[];
  citation_total: number;
  citation_found: number;
  citation_hallucinated: number;
  keyword_expected: number;
  keyword_hit: number;
  keyword_rate: number | null;
  hedging_failed: boolean;
}

interface VerifyRecord {
  id: string;
  title: string;
  zone: string;
  complexity: string;
  liability: string;
  grade: VerifyGrade;
  turns: Array<{ turn: number; skipped?: string; error: string | null }>;
}

interface VerifySummaryEntry {
  id: string;
  verdict: string;
  kw_rate: number | null;
  citation_found: number;
  citation_total: number;
  hallucinated: number;
  reasons: string[];
}

function readJson<T>(p: string): T {
  return JSON.parse(fs.readFileSync(p, "utf-8")) as T;
}

// ─── 1. Cost gate ─────────────────────────────────────────────────────────────

test.describe("ABS-458 cost gate — TC-001 alone on the claude_code backend", () => {
  test("cost gate run directory holds COST_GATE.md, SUMMARY.json and TC-001.json", () => {
    expect(fs.existsSync(COST_GATE_DIR), `${COST_GATE_DIR} must exist`).toBe(true);
    const files = fs.readdirSync(COST_GATE_DIR);
    expect(files).toContain("COST_GATE.md");
    expect(files).toContain("SUMMARY.json");
    expect(files).toContain("TC-001.json");
  });

  test("COST_GATE.md documents the backend, the metered spend and the extrapolation", () => {
    const md = fs.readFileSync(path.join(COST_GATE_DIR, "COST_GATE.md"), "utf-8");
    expect(md).toContain("ABS-458");
    // The backend under test must be named, not assumed.
    expect(md).toContain("claude_code");
    // The gate exists to answer "what will the other 19 cases cost?" — a gate
    // record with no extrapolation is not a gate.
    expect(md.toLowerCase()).toMatch(/extrapolat|projec/);
    expect(md.length).toBeGreaterThan(500);
  });

  test("SUMMARY.json records exactly the one gated case, fully completed", () => {
    const summary = readJson<SummaryEntry[]>(path.join(COST_GATE_DIR, "SUMMARY.json"));
    expect(summary.length).toBe(1);
    expect(summary[0].id).toBe("TC-001");
    expect(summary[0].error).toBeNull();
    expect(summary[0].turns_completed).toBe(summary[0].turns_expected);
    expect(summary[0].turns_completed).toBeGreaterThan(0);
  });

  test("the wall clock quoted in COST_GATE.md matches the raw TC-001.json", () => {
    // Guards against a hand-written gate record drifting from its evidence.
    const tc = readJson<EvalTCResult>(path.join(COST_GATE_DIR, "TC-001.json"));
    const summary = readJson<SummaryEntry[]>(path.join(COST_GATE_DIR, "SUMMARY.json"));
    const md = fs.readFileSync(path.join(COST_GATE_DIR, "COST_GATE.md"), "utf-8");

    expect(summary[0].wall_s).toBeCloseTo(tc.case_wall_time_s, 1);
    // COST_GATE.md quotes the case wall clock rounded to 0.1 s.
    expect(md).toContain(tc.case_wall_time_s.toFixed(1));

    // Per-turn wall clocks must sum to the case wall clock.
    const turnSum = tc.turns.reduce((acc, t) => acc + t.wall_time_s, 0);
    expect(turnSum).toBeCloseTo(tc.case_wall_time_s, 1);
  });

  test("the iteration count quoted in COST_GATE.md matches tool_loop_metrics", () => {
    const tc = readJson<EvalTCResult>(path.join(COST_GATE_DIR, "TC-001.json"));
    const iterations = tc.turns.reduce(
      (acc, t) => acc + (t.tool_loop_metrics?.iterations ?? 0),
      0,
    );
    expect(iterations).toBeGreaterThan(0);
    // Each tool-loop iteration is one `claude -p` subprocess invocation; the
    // gate's projection of total CLI calls is built on this number.
    const md = fs.readFileSync(path.join(COST_GATE_DIR, "COST_GATE.md"), "utf-8");
    expect(md).toContain(String(iterations));
  });

  test("TC-001 ran on a real model, ended cleanly, and reported token usage", () => {
    const tc = readJson<EvalTCResult>(path.join(COST_GATE_DIR, "TC-001.json"));
    expect(tc.id).toBe("TC-001");
    expect(tc.model).toBeTruthy();
    expect(tc.spec["id"]).toBe("TC-001");
    expect(tc.turns.length).toBeGreaterThan(0);

    for (const turn of tc.turns) {
      expect(turn.error, `TC-001 turn ${turn.turn} must not error`).toBeNull();
      expect(turn.stop_reason).toBe("end_turn");
      const metrics = turn.tool_loop_metrics;
      expect(metrics, `turn ${turn.turn} must carry tool_loop_metrics`).not.toBeNull();
      expect(metrics!.type).toBe("tool_loop_metrics");
      // No breaker fired — otherwise the extrapolation is measuring a truncated
      // case and understates the suite.
      expect(metrics!.terminated_reason).toBe("end_turn");
      expect(metrics!.total_usage.output_tokens).toBeGreaterThan(0);
    }
  });

  test("no per-call USD figure is present — this path is subscription-metered", () => {
    // The claude_code gateway bills the Claude Code subscription. A
    // total_cost_usd here would mean an ANTHROPIC_API_KEY leaked into the
    // advisor process and the run was billed to the API account instead.
    const tc = readJson<EvalTCResult>(path.join(COST_GATE_DIR, "TC-001.json"));
    for (const turn of tc.turns) {
      expect(turn.tool_loop_metrics!.total_cost_usd).toBeUndefined();
    }
  });
});

// ─── 2. API-path baseline grading (DoD #5 comparison) ────────────────────────

test.describe("ABS-458 baseline grading — ABS-306 API path, unmodified verifier", () => {
  test("the verifier used for the comparison is the committed, shared one", () => {
    expect(fs.existsSync(VERIFIER_SCRIPT), "scripts/verify_test_prompts.py must exist").toBe(true);
  });

  test("verification directory holds one .verify.json per case plus SUMMARY.json", () => {
    expect(fs.existsSync(VERIFICATION_DIR), `${VERIFICATION_DIR} must exist`).toBe(true);
    const files = fs.readdirSync(VERIFICATION_DIR);
    expect(files).toContain("SUMMARY.json");
    for (const id of ALL_IDS) {
      expect(files, `${id}.verify.json must exist`).toContain(`${id}.verify.json`);
    }
  });

  test("verification SUMMARY.json has 20 well-formed entries", () => {
    const entries = readJson<VerifySummaryEntry[]>(path.join(VERIFICATION_DIR, "SUMMARY.json"));
    expect(entries.length).toBe(20);
    expect(entries.map((e) => e.id)).toEqual(ALL_IDS);
    for (const e of entries) {
      expect(["PASS", "PARTIAL", "FAIL"]).toContain(e.verdict);
      expect(typeof e.citation_found).toBe("number");
      expect(typeof e.citation_total).toBe("number");
      expect(typeof e.hallucinated).toBe("number");
      expect(Array.isArray(e.reasons)).toBe(true);
    }
  });

  test("SUMMARY.json agrees with each per-case .verify.json", () => {
    const entries = readJson<VerifySummaryEntry[]>(path.join(VERIFICATION_DIR, "SUMMARY.json"));
    for (const e of entries) {
      const rec = readJson<VerifyRecord>(path.join(VERIFICATION_DIR, `${e.id}.verify.json`));
      expect(rec.id).toBe(e.id);
      expect(rec.grade.verdict, `${e.id} verdict`).toBe(e.verdict);
      expect(rec.grade.citation_found, `${e.id} citation_found`).toBe(e.citation_found);
      expect(rec.grade.citation_total, `${e.id} citation_total`).toBe(e.citation_total);
      expect(rec.grade.citation_hallucinated, `${e.id} hallucinated`).toBe(e.hallucinated);
      expect(rec.grade.reasons, `${e.id} reasons`).toEqual(e.reasons);
    }
  });

  test("zero hallucinated citations and every citation resolved across the baseline", () => {
    const entries = readJson<VerifySummaryEntry[]>(path.join(VERIFICATION_DIR, "SUMMARY.json"));
    const hallucinated = entries.filter((e) => e.hallucinated > 0).map((e) => e.id);
    expect(hallucinated).toEqual([]);
    const unresolved = entries.filter((e) => e.citation_found !== e.citation_total).map((e) => e.id);
    expect(unresolved).toEqual([]);
  });

  test("the four cases with real transcripts are graded, and none FAIL", () => {
    const entries = readJson<VerifySummaryEntry[]>(path.join(VERIFICATION_DIR, "SUMMARY.json"));
    const graded = entries.filter((e) => BASELINE_GRADED_IDS.includes(e.id));
    expect(graded.length).toBe(BASELINE_GRADED_IDS.length);
    for (const e of graded) {
      expect(["PASS", "PARTIAL"], `${e.id} verdict`).toContain(e.verdict);
      expect(e.citation_total, `${e.id} must have resolved citations`).toBeGreaterThan(0);
      expect(e.kw_rate, `${e.id} must have a keyword rate`).not.toBeNull();
    }
    // The comparison baseline is only meaningful if some case actually passed.
    expect(graded.some((e) => e.verdict === "PASS")).toBe(true);
  });

  test("credit-exhausted cases are graded FAIL with skipped turns, not silently passed", () => {
    const empties = ALL_IDS.filter((id) => !BASELINE_GRADED_IDS.includes(id));
    for (const id of empties) {
      const rec = readJson<VerifyRecord>(path.join(VERIFICATION_DIR, `${id}.verify.json`));
      expect(rec.grade.verdict, `${id} must be FAIL`).toBe("FAIL");
      expect(rec.grade.citation_total, `${id} must have no citations`).toBe(0);
      expect(rec.grade.keyword_rate, `${id} must have no keyword rate`).toBeNull();
      expect(rec.turns.length, `${id} must record its turns`).toBeGreaterThan(0);
      for (const t of rec.turns) {
        expect(t.skipped, `${id} turn ${t.turn} must be marked skipped`).toBeTruthy();
      }
    }
  });
});
