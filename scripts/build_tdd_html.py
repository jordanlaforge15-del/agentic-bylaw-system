#!/usr/bin/env python3
"""Render docs/TDD.md into a self-contained docs/TDD.html.

The output loads ``mermaid.min.js`` and ``marked.min.js`` from
``cdn.jsdelivr.net`` and renders the markdown in the browser at page
load. Mermaid fenced blocks are turned into ``<pre class="mermaid">``
nodes after marked parses them, which is what mermaid v10+ looks for.

Run as part of the doc build:

    python scripts/build_tdd_html.py

Or with explicit paths:

    python scripts/build_tdd_html.py docs/TDD.md docs/TDD.html
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

HTML_TEMPLATE = """<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>Agentic Bylaw System — Technical Design Document</title>
  <style>
    :root {{
      --bg: #ffffff;
      --fg: #1f2328;
      --muted: #57606a;
      --border: #d0d7de;
      --code-bg: #f6f8fa;
      --link: #0969da;
      --max-width: 980px;
    }}
    * {{ box-sizing: border-box; }}
    html, body {{ margin: 0; padding: 0; background: var(--bg); color: var(--fg); }}
    body {{
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", "Helvetica Neue", Arial, sans-serif;
      line-height: 1.55;
      padding: 32px 24px 96px;
    }}
    main {{ max-width: var(--max-width); margin: 0 auto; }}
    h1, h2, h3, h4 {{ line-height: 1.25; margin-top: 1.6em; margin-bottom: 0.4em; }}
    h1 {{ font-size: 2rem; border-bottom: 1px solid var(--border); padding-bottom: 0.3em; }}
    h2 {{ font-size: 1.5rem; border-bottom: 1px solid var(--border); padding-bottom: 0.2em; }}
    h3 {{ font-size: 1.2rem; }}
    a {{ color: var(--link); text-decoration: none; }}
    a:hover {{ text-decoration: underline; }}
    code {{
      font-family: "SFMono-Regular", Menlo, Consolas, "Liberation Mono", monospace;
      background: var(--code-bg);
      padding: 0.15em 0.35em;
      border-radius: 4px;
      font-size: 0.92em;
    }}
    pre code {{ background: transparent; padding: 0; }}
    pre {{
      background: var(--code-bg);
      padding: 12px 16px;
      border-radius: 6px;
      overflow-x: auto;
      font-size: 0.9em;
    }}
    pre.mermaid {{
      background: transparent;
      padding: 0;
      text-align: center;
    }}
    table {{ border-collapse: collapse; margin: 0.8em 0; }}
    th, td {{ border: 1px solid var(--border); padding: 6px 10px; text-align: left; }}
    blockquote {{
      color: var(--muted);
      border-left: 3px solid var(--border);
      margin: 0.8em 0;
      padding: 0.2em 0.8em;
    }}
    hr {{ border: 0; border-top: 1px solid var(--border); margin: 2em 0; }}
    header.meta {{
      max-width: var(--max-width);
      margin: 0 auto 24px;
      color: var(--muted);
      font-size: 0.85rem;
    }}
  </style>
</head>
<body>
  <header class="meta">
    Rendered from <code>docs/TDD.md</code>. Mermaid.js loaded from CDN — diagrams render on page load with no extensions.
  </header>
  <main id="content">Loading…</main>

  <script id="tdd-markdown" type="text/markdown">{markdown_payload}</script>

  <script src="https://cdn.jsdelivr.net/npm/marked/marked.min.js"></script>
  <script src="https://cdn.jsdelivr.net/npm/mermaid/dist/mermaid.min.js"></script>
  <script>
    (function () {{
      const source = document.getElementById("tdd-markdown").textContent;
      // marked v5+ exposes parse(); older builds expose marked() directly.
      const parse = (typeof marked === "function") ? marked : marked.parse;
      let html = parse(source);
      // marked emits <pre><code class="language-mermaid">...</code></pre>
      // for fenced ```mermaid``` blocks. Mermaid v10+ scans for
      // <pre class="mermaid">, so rewrite the wrapper before render.
      html = html.replace(
        /<pre><code class="language-mermaid">([\\s\\S]*?)<\\/code><\\/pre>/g,
        function (_m, body) {{
          const decoded = body
            .replace(/&lt;/g, "<")
            .replace(/&gt;/g, ">")
            .replace(/&quot;/g, '"')
            .replace(/&#39;/g, "'")
            .replace(/&amp;/g, "&");
          return '<pre class="mermaid">' + decoded + '</pre>';
        }}
      );
      document.getElementById("content").innerHTML = html;
      if (window.mermaid && typeof window.mermaid.initialize === "function") {{
        window.mermaid.initialize({{ startOnLoad: false, theme: "default", securityLevel: "loose" }});
        window.mermaid.run({{ querySelector: "pre.mermaid" }});
      }}
    }})();
  </script>
</body>
</html>
"""


def build(src: Path, dst: Path) -> None:
    markdown = src.read_text(encoding="utf-8")
    # json.dumps gives us a safely-escaped JS string. We strip the
    # outer quotes and let the <script type="text/markdown"> tag hold
    # the raw text — but we also need to neutralise any literal
    # ``</script>`` that might appear in code blocks. JSON encoding
    # handles backslashes, quotes, and newlines for free; for </script
    # we explicitly break the tag with a zero-width split.
    safe_markdown = markdown.replace("</script>", "<\\/script>")
    dst.write_text(HTML_TEMPLATE.format(markdown_payload=safe_markdown), encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("src", nargs="?", default="docs/TDD.md", type=Path)
    parser.add_argument("dst", nargs="?", default="docs/TDD.html", type=Path)
    args = parser.parse_args()
    build(args.src, args.dst)
    print(f"build_tdd_html: wrote {args.dst}")


if __name__ == "__main__":
    main()
