// ABS-82: the TDD docs (docs/TDD.md + docs/TDD.html) are the
// architectural reference for the system. This spec asserts:
//
//   1. docs/TDD.md exists, contains every required section, and has at
//      least one ```mermaid``` block per the issue spec (graph TD,
//      flowchart LR x3, erDiagram, sequenceDiagram, graph LR — 7 min).
//   2. docs/TDD.html exists, is a self-contained doc that loads
//      mermaid.js + marked.js from cdn.jsdelivr.net, and embeds the
//      markdown payload from TDD.md so the diagrams render in any
//      browser with no extensions.
//   3. The HTML loads via file:// in a real browser and the embedded
//      markdown is converted into <pre class="mermaid"> nodes (the
//      structural precondition for mermaid.run to render diagrams).
//
// Mermaid syntax itself is verified server-side by
// scripts/validate_tdd_mermaid.sh (npx -y @mermaid-js/mermaid-cli on
// each block) — this spec covers the markdown structure and HTML
// wiring, which mmdc can't see.

import * as fs from "node:fs";
import * as path from "node:path";

import { expect, test } from "../fixtures/test-env";

const REPO_ROOT = path.resolve(__dirname, "..", "..", "..");
const TDD_MD = path.join(REPO_ROOT, "docs", "TDD.md");
const TDD_HTML = path.join(REPO_ROOT, "docs", "TDD.html");

const REQUIRED_SECTIONS = [
  "System overview and goals",
  "High-level architecture",
  "Ingestion pipelines",
  "Data model",
  "Retrieval API",
  "MCP integration surface",
  "Deployment topology",
];

const REQUIRED_DIAGRAM_KINDS = [
  "graph TD",
  "flowchart LR",
  "erDiagram",
  "sequenceDiagram",
  "graph LR",
];

function readMermaidBlocks(md: string): string[] {
  const blocks: string[] = [];
  const re = /```mermaid\n([\s\S]*?)\n```/g;
  let match: RegExpExecArray | null;
  while ((match = re.exec(md)) !== null) {
    blocks.push(match[1]);
  }
  return blocks;
}

test.describe("ABS-82 TDD docs", () => {
  test("docs/TDD.md has every required section and Mermaid block kind", () => {
    expect(fs.existsSync(TDD_MD), `${TDD_MD} should exist`).toBe(true);
    const md = fs.readFileSync(TDD_MD, "utf-8");

    for (const heading of REQUIRED_SECTIONS) {
      expect(
        md.includes(heading),
        `TDD.md should contain section "${heading}"`,
      ).toBe(true);
    }

    const blocks = readMermaidBlocks(md);
    expect(
      blocks.length,
      "TDD.md should contain at least 7 mermaid blocks",
    ).toBeGreaterThanOrEqual(7);

    for (const kind of REQUIRED_DIAGRAM_KINDS) {
      const hasKind = blocks.some((b) => b.trimStart().startsWith(kind));
      expect(hasKind, `TDD.md should contain a ${kind} block`).toBe(true);
    }

    // Each block must at least cite something from src/ or mcp/ or
    // alembic/ somewhere in the doc body — the issue requires "Cite
    // specific files and functions for each architectural claim."
    // Soft sanity: assert overall citation density rather than per-block.
    const citationHits = (md.match(/src\/[\w/.]+\.(py|ts)/g) || []).length;
    expect(
      citationHits,
      "TDD.md should cite many specific src/ files (>= 25)",
    ).toBeGreaterThanOrEqual(25);
  });

  test("docs/TDD.html is self-contained and loads mermaid from CDN", () => {
    expect(fs.existsSync(TDD_HTML), `${TDD_HTML} should exist`).toBe(true);
    const html = fs.readFileSync(TDD_HTML, "utf-8");

    expect(
      html,
      "TDD.html should load mermaid.min.js from cdn.jsdelivr.net",
    ).toContain("https://cdn.jsdelivr.net/npm/mermaid/dist/mermaid.min.js");

    expect(
      html,
      "TDD.html should load marked.min.js from cdn.jsdelivr.net so it can parse the embedded markdown",
    ).toContain("https://cdn.jsdelivr.net/npm/marked/marked.min.js");

    expect(
      html,
      "TDD.html should initialize mermaid and run it against pre.mermaid",
    ).toContain("mermaid.initialize");
    expect(html).toContain("mermaid.run");

    // The markdown payload is embedded in a <script type="text/markdown">
    // tag so the HTML is fully self-contained. Spot-check that the
    // payload contains a section heading + a mermaid block opener.
    expect(
      html,
      "TDD.html should embed the TDD.md content in a script tag",
    ).toMatch(/<script[^>]*type="text\/markdown"[^>]*>/);
    expect(html).toContain("System overview and goals");
    expect(html).toContain("```mermaid");
  });

  test("docs/TDD.html parses its embedded markdown into mermaid blocks", async ({
    page,
  }) => {
    // Loading the file:// URL exercises the actual page bootstrap
    // path: marked parses the markdown, the inline script rewrites
    // ```mermaid``` code blocks into <pre class="mermaid"> nodes, and
    // (if cdn.jsdelivr.net is reachable) mermaid.run renders them to
    // SVG. We assert on the structural precondition only — the
    // <pre class="mermaid"> rewrite — because the mermaid SVG render
    // depends on outbound network to the CDN, which is not guaranteed
    // in every test environment. Mermaid syntax itself is verified by
    // scripts/validate_tdd_mermaid.sh.
    const fileUrl = `file://${TDD_HTML}`;
    await page.goto(fileUrl);
    // Give marked.js a beat to parse the markdown payload.
    await page.waitForFunction(
      () => document.querySelectorAll("pre.mermaid").length > 0,
      undefined,
      { timeout: 10_000 },
    );
    const mermaidCount = await page
      .locator("pre.mermaid")
      .count();
    expect(mermaidCount).toBeGreaterThanOrEqual(7);

    // The first <h1> should be the TDD title — confirms marked
    // actually produced HTML from the markdown payload.
    await expect(
      page.getByRole("heading", { level: 1, name: /Technical Design Document/i }),
    ).toBeVisible();
  });
});
