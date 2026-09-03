// Regenerates web/public/og-image.png — the 1200x630 social card that
// every page's og:image and twitter:image points at (ABS-510).
//
// Run from web/:  node scripts/generate-og-image.mjs
//
// Why a committed PNG rather than a `next/og` route: a static file is
// served by the same handler as the favicon, cannot time out, cannot be
// hammered, and needs no runtime edge dependency. The cost is that the
// card only changes when someone reruns this script — which is the right
// trade for a brand card that changes once a year.
//
// The render uses the Playwright Chromium already installed for the e2e
// suite. Fonts are the system stack, not the Google-hosted Inter Tight /
// JetBrains Mono the site loads: the generator must work offline, and
// nobody can tell the difference at this size on a link preview. Colours
// are the "Setback" dark theme tokens from app/globals.css, kept in sync
// by hand — a card is not worth a build step.

import { chromium } from "@playwright/test";
import { mkdir, writeFile } from "node:fs/promises";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const WEB_ROOT = join(dirname(fileURLToPath(import.meta.url)), "..");
const OUTPUT = join(WEB_ROOT, "public", "og-image.png");

const WIDTH = 1200;
const HEIGHT = 630;

// app/globals.css [data-mode="dark"].
const SURFACE = "#0a0a0a";
const INK = "#ede8db";
const MUTED = "#9a9484";
const ACCENT = "#c9f24c";
const HAIR = "rgba(237, 232, 219, 0.12)";

const HTML = `<!doctype html>
<html><head><meta charset="utf-8"><style>
  * { margin: 0; padding: 0; box-sizing: border-box; }
  html, body { width: ${WIDTH}px; height: ${HEIGHT}px; }
  body {
    background: ${SURFACE};
    color: ${INK};
    font-family: "Helvetica Neue", Helvetica, Arial, sans-serif;
    -webkit-font-smoothing: antialiased;
    display: flex;
  }
  /* No border-radius anywhere — sharp corners are part of the brand. */
  .card {
    flex: 1;
    margin: 40px;
    border: 1px solid ${HAIR};
    padding: 56px 64px;
    display: flex;
    flex-direction: column;
    justify-content: space-between;
  }
  .kicker {
    font-family: "SF Mono", Menlo, Consolas, monospace;
    font-size: 20px;
    letter-spacing: 0.22em;
    text-transform: uppercase;
    color: ${MUTED};
  }
  .wordmark {
    font-size: 168px;
    font-weight: 800;
    letter-spacing: -0.05em;
    line-height: 0.9;
  }
  .wordmark span { color: ${ACCENT}; }
  .headline {
    font-size: 46px;
    font-weight: 600;
    letter-spacing: -0.02em;
    line-height: 1.15;
    max-width: 900px;
    margin-top: 28px;
  }
  .footer {
    display: flex;
    align-items: center;
    gap: 20px;
    border-top: 1px solid ${HAIR};
    padding-top: 26px;
  }
  .bar { width: 56px; height: 10px; background: ${ACCENT}; flex: none; }
  .footer p {
    font-family: "SF Mono", Menlo, Consolas, monospace;
    font-size: 21px;
    letter-spacing: 0.02em;
    color: ${MUTED};
  }
  .footer b { color: ${INK}; font-weight: 400; }
</style></head>
<body>
  <div class="card">
    <div>
      <div class="kicker">Agentic Bylaw System</div>
    </div>
    <div>
      <div class="wordmark">ABS<span>°</span></div>
      <div class="headline">An expert planner, integrated into your workflow.</div>
    </div>
    <div class="footer">
      <div class="bar"></div>
      <p>Halifax Regional Centre Land Use By-law &nbsp;·&nbsp; <b>cited to the clause</b></p>
    </div>
  </div>
</body></html>`;

const browser = await chromium.launch();
const page = await browser.newPage({
  viewport: { width: WIDTH, height: HEIGHT },
  deviceScaleFactor: 1,
});
await page.setContent(HTML, { waitUntil: "load" });
const png = await page.screenshot({ type: "png" });
await browser.close();

await mkdir(dirname(OUTPUT), { recursive: true });
await writeFile(OUTPUT, png);
console.log(`wrote ${OUTPUT} (${WIDTH}x${HEIGHT}, ${png.length} bytes)`);
