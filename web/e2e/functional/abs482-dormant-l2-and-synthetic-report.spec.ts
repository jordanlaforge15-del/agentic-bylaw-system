// ABS-482: the Layer 2 answer pipeline is dormant, and a synthetic accuracy
// report must never look like a measurement.
//
// Two documentation defects shipped together:
//
//   1. docs/architecture.md described the `fragment_embedding → prompt_log →
//      answer_log → generated_claim → feedback` pipeline as *the* answer path,
//      with nothing saying that no production code calls it. Live questions are
//      served by the MCP retrieval service; the Layer 2 pipeline runs only from
//      `src/layer2/cli.py` and the offline eval harness.
//
//   2. docs/extraction/pdf_accuracy_2026-05-26.md reported 90% precision and a
//      "✅ PASS" exit gate. It was `--demo` output — hardcoded fixtures scored
//      against hardcoded ground truth — so every number in it was a property of
//      the fixtures, not of the extraction pipeline.
//
// Asserts here:
//   1. Both places in architecture.md carry the dormancy statement and name the
//      live path (`mcp/bylaw_retrieval/retrieval/service.py`).
//   2. The dormancy claim is *true*, not just written down: the Layer 2 answer
//      tables are referenced nowhere outside `src/layer2/` — not in the MCP
//      server, not in the web app. This is the assertion that rots if someone
//      wires the pipeline up and forgets the doc.
//   3. No `pdf_accuracy_*.md` is checked into `docs/extraction/`, and
//      .gitignore keeps generated ones out.
//   4. Running the harness in `--demo` mode really does produce a
//      self-labelling report: SYNTHETIC filename, banner above the summary,
//      tagged title, and a qualified exit-gate heading. The gate line was the
//      specific thing being misread, so it is asserted directly.
//
// Repo/CLI checks — no running server, no database, no network, and (with
// --no-llm) no model calls. The harness run takes ~0.1s.
//
// This spec deliberately does NOT shell out to pytest; see the note in
// abs463-bylaw-reference-validation.spec.ts for why a pytest session inside a
// Playwright worker starves the WebKit projects. Invoking one short-lived
// offline script is a different cost class.

import { execFileSync } from "child_process";
import * as fs from "fs";
import * as os from "os";
import * as path from "path";
import { expect, test } from "@playwright/test";

const REPO_ROOT = path.resolve(__dirname, "../../../");
const ARCHITECTURE_DOC = path.join(REPO_ROOT, "docs", "architecture.md");
const EXTRACTION_DOCS = path.join(REPO_ROOT, "docs", "extraction");
const GITIGNORE = path.join(REPO_ROOT, ".gitignore");
const EVAL_SCRIPT = path.join(REPO_ROOT, "scripts", "pdf_extraction_eval.py");

const LIVE_ANSWER_PATH = "mcp/bylaw_retrieval/retrieval/service.py";

// Tables written only by the dormant Layer 2 answer pipeline. `fragment_embedding`
// is deliberately absent: src/layer1/pipeline/page_break_repair.py issues a guarded
// DELETE against it when it re-splits fragments, which invalidates stale embeddings
// rather than driving the answer pipeline. Writing a prompt_log/answer_log/
// generated_claim row, by contrast, is only something a caller of the pipeline does.
const DORMANT_TABLES = ["prompt_log", "answer_log", "generated_claim"];

function readFile(p: string): string {
  return fs.readFileSync(p, "utf-8");
}

/** Files under `dir` matching `exts`, skipping vendored/build directories. */
function walk(dir: string, exts: string[]): string[] {
  const skip = new Set([
    "node_modules",
    ".next",
    ".next-e2e",
    "__pycache__",
    ".venv",
    "dist",
    "build",
    "test-results",
    "playwright-report",
  ]);
  const out: string[] = [];
  const stack = [dir];
  while (stack.length) {
    const current = stack.pop()!;
    let entries: fs.Dirent[];
    try {
      entries = fs.readdirSync(current, { withFileTypes: true });
    } catch {
      continue;
    }
    for (const entry of entries) {
      const full = path.join(current, entry.name);
      if (entry.isDirectory()) {
        if (!skip.has(entry.name) && !entry.name.startsWith(".")) stack.push(full);
      } else if (exts.some((ext) => entry.name.endsWith(ext))) {
        out.push(full);
      }
    }
  }
  return out;
}

/** The repo venv per CLAUDE.md; fall back to the ambient interpreter. */
function pythonBin(): string {
  const venv = path.join(REPO_ROOT, ".venv", "bin", "python");
  return fs.existsSync(venv) ? venv : "python3";
}

test.describe("ABS-482: dormant Layer 2 pipeline is documented as dormant", () => {
  test("the Layer 2 intro says dormant and names the live answer path", () => {
    const doc = readFile(ARCHITECTURE_DOC);
    // The intro paragraph, before the first "## " section heading.
    const intro = doc.split(/^## /m)[0];

    expect(intro).toMatch(/Layer 2 answer pipeline is dormant/i);
    expect(intro).toContain(LIVE_ANSWER_PATH);
  });

  test("the Layer 2 Flow section is fronted by a dormancy banner", () => {
    const doc = readFile(ARCHITECTURE_DOC);
    const sections = doc.split(/^## /m);
    const flow = sections.find((s) => s.startsWith("Layer 2 Flow"));
    expect(flow, "docs/architecture.md must have a '## Layer 2 Flow' section").toBeTruthy();

    // The banner must precede the numbered steps it qualifies — a note tacked
    // on after step 7 is one nobody reads before believing steps 1..7.
    const firstStep = flow!.indexOf("\n1. ");
    expect(firstStep).toBeGreaterThan(0);
    const banner = flow!.slice(0, firstStep);

    expect(banner).toMatch(/dormant/i);
    expect(banner).toMatch(/no production caller/i);
    expect(banner).toContain(LIVE_ANSWER_PATH);
    // Points at where the pipeline *can* be reached from, so "dormant" is a
    // statement about callers rather than about the code being deleted.
    expect(banner).toContain("src/layer2/cli.py");
    expect(banner).toContain("src/layer2/eval/harness.py");
    // Every line of the banner is blockquoted, so it renders as a callout.
    // `.slice(1)` drops the "Layer 2 Flow" heading text the split left behind.
    for (const line of banner.trim().split("\n").slice(1)) {
      if (line.trim() === "") continue;
      expect(line.startsWith(">"), `banner line not blockquoted: ${line}`).toBe(true);
    }
  });

  test("no production code outside src/layer2 touches the answer tables", () => {
    const candidates = [
      ...walk(path.join(REPO_ROOT, "src"), [".py"]).filter(
        (f) => !f.startsWith(path.join(REPO_ROOT, "src", "layer2") + path.sep),
      ),
      ...walk(path.join(REPO_ROOT, "mcp"), [".py"]),
      ...walk(path.join(REPO_ROOT, "web", "app"), [".ts", ".tsx"]),
      ...walk(path.join(REPO_ROOT, "web", "components"), [".ts", ".tsx"]),
      ...walk(path.join(REPO_ROOT, "web", "lib"), [".ts", ".tsx"]),
    ];
    expect(candidates.length, "expected to have scanned some source files").toBeGreaterThan(0);

    const hits: string[] = [];
    for (const file of candidates) {
      const body = readFile(file);
      for (const table of DORMANT_TABLES) {
        if (new RegExp(`\\b${table}\\b`).test(body)) {
          hits.push(`${path.relative(REPO_ROOT, file)} → ${table}`);
        }
      }
    }

    // If this fails, the pipeline has gained a caller: the architecture doc's
    // dormancy banner is now wrong and must be rewritten, not this assertion
    // relaxed.
    expect(hits, "Layer 2 answer tables referenced outside src/layer2").toEqual([]);
  });
});

test.describe("ABS-482: synthetic accuracy reports self-label", () => {
  test("no accuracy report is checked into docs/extraction", () => {
    const entries = fs.existsSync(EXTRACTION_DOCS) ? fs.readdirSync(EXTRACTION_DOCS) : [];
    const reports = entries.filter((f) => /^pdf_accuracy_.*\.md$/.test(f));
    expect(reports, "generated accuracy reports must not be committed").toEqual([]);
  });

  test(".gitignore keeps generated reports out of the repo", () => {
    expect(readFile(GITIGNORE)).toContain("docs/extraction/pdf_accuracy_*.md");
  });

  test("--demo writes a SYNTHETIC-named, SYNTHETIC-banner report", () => {
    const outDir = fs.mkdtempSync(path.join(os.tmpdir(), "abs482-"));
    try {
      execFileSync(
        pythonBin(),
        [EVAL_SCRIPT, "--demo", "--no-llm", "--out", outDir],
        { cwd: REPO_ROOT, encoding: "utf-8", stdio: "pipe" },
      );

      const written = fs.readdirSync(outDir).filter((f) => f.endsWith(".md"));
      expect(written).toHaveLength(1);
      // The filename alone has to carry the warning: reports get linked and
      // pasted by name long before anyone opens them.
      expect(written[0]).toMatch(/^pdf_accuracy_SYNTHETIC_\d{4}-\d{2}-\d{2}\.md$/);

      const body = readFile(path.join(outDir, written[0]));
      expect(body).toMatch(/^# PDF Extraction Accuracy Report \[SYNTHETIC\] — /m);
      expect(body).toContain("> **SYNTHETIC — demo fixtures, not a measurement.**");
      expect(body).toMatch(/Do not cite it as accuracy\s*\n?>?\s*evidence/);

      // The banner precedes the numbers it qualifies.
      const bannerAt = body.indexOf("SYNTHETIC — demo fixtures");
      const summaryAt = body.indexOf("## Summary");
      expect(summaryAt).toBeGreaterThan(-1);
      expect(bannerAt).toBeLessThan(summaryAt);

      // The "✅ PASS" gate verdict was the specific line being read as
      // evidence, so the heading above it must disown it.
      expect(body).toContain(
        "## Exit Gate Status (Phase 3 PDF Track) — SYNTHETIC, NOT A GATE RESULT",
      );
    } finally {
      fs.rmSync(outDir, { recursive: true, force: true });
    }
  });
});
