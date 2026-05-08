// Markdown renderer for agent message bodies. Wraps react-markdown +
// remark-gfm so LLM output (which leans heavily on markdown tables,
// bold, lists, and inline code) renders the way a human would expect
// rather than as a glob of asterisks and pipe characters.
//
// Component overrides keep the brand: hairline borders, sharp corners
// (no border-radius anywhere), JetBrains Mono for code blocks, the
// accent colour for emphasis. We deliberately do *not* allow raw HTML
// — the LLM's output isn't trusted enough to bypass markdown's escape
// rules.

"use client";

import ReactMarkdown, { type Components } from "react-markdown";
import remarkGfm from "remark-gfm";

const COMPONENTS: Components = {
  h1: ({ children }) => (
    <h1
      className="font-sans font-extrabold mt-3 mb-1.5"
      style={{
        fontSize: 22,
        letterSpacing: "-0.025em",
        lineHeight: 1.15,
      }}
    >
      {children}
    </h1>
  ),
  h2: ({ children }) => (
    <h2
      className="font-sans font-bold mt-3 mb-1.5"
      style={{
        fontSize: 18,
        letterSpacing: "-0.02em",
        lineHeight: 1.2,
      }}
    >
      {children}
    </h2>
  ),
  h3: ({ children }) => (
    <h3
      className="font-sans font-semibold mt-2.5 mb-1"
      style={{ fontSize: 15, letterSpacing: "-0.015em" }}
    >
      {children}
    </h3>
  ),
  p: ({ children }) => <p className="my-2">{children}</p>,
  strong: ({ children }) => (
    <strong className="font-semibold text-text">{children}</strong>
  ),
  em: ({ children }) => <em className="italic">{children}</em>,
  ul: ({ children }) => <ul className="list-disc pl-6 my-2">{children}</ul>,
  ol: ({ children }) => <ol className="list-decimal pl-6 my-2">{children}</ol>,
  li: ({ children }) => <li className="my-0.5">{children}</li>,
  hr: () => <hr className="border-0 border-t border-hair my-4" />,
  blockquote: ({ children }) => (
    <blockquote
      className="my-2 pl-3 italic text-text-muted"
      style={{ borderLeft: "2px solid var(--accent)" }}
    >
      {children}
    </blockquote>
  ),
  a: ({ children, href }) => (
    <a
      href={href}
      target="_blank"
      rel="noopener noreferrer"
      className="text-text underline underline-offset-2 hover:text-accent-ink"
    >
      {children}
    </a>
  ),
  code: ({ className, children, ...props }) => {
    // ReactMarkdown distinguishes inline vs block code by whether the
    // surrounding parent is <pre>. Block code gets className with a
    // "language-..." prefix; inline code does not. We style each
    // distinctly: inline is a thin pill, block is a monospaced
    // surface-alt panel.
    const isBlock = /language-/.test(className || "");
    if (isBlock) {
      return (
        <code
          className={className}
          style={{
            display: "block",
            background: "var(--surface-alt)",
            border: "1px solid var(--hair)",
            padding: "10px 12px",
            fontFamily: "var(--font-mono), monospace",
            fontSize: 12.5,
            lineHeight: 1.5,
            overflowX: "auto",
            whiteSpace: "pre",
          }}
          {...props}
        >
          {children}
        </code>
      );
    }
    return (
      <code
        className="font-mono"
        style={{
          background: "var(--surface-alt)",
          border: "1px solid var(--hair)",
          padding: "1px 5px",
          fontSize: "0.9em",
        }}
        {...props}
      >
        {children}
      </code>
    );
  },
  pre: ({ children }) => <pre className="my-2">{children}</pre>,
  table: ({ children }) => (
    <div className="my-3 overflow-x-auto">
      <table
        className="border-collapse"
        style={{
          border: "1px solid var(--hair)",
          fontSize: 13,
          minWidth: "100%",
        }}
      >
        {children}
      </table>
    </div>
  ),
  thead: ({ children }) => (
    <thead style={{ background: "var(--surface-alt)" }}>{children}</thead>
  ),
  th: ({ children }) => (
    <th
      className="font-mono text-left text-text-muted"
      style={{
        border: "1px solid var(--hair)",
        padding: "7px 10px",
        fontSize: 10.5,
        letterSpacing: "0.08em",
        textTransform: "uppercase",
        fontWeight: 500,
      }}
    >
      {children}
    </th>
  ),
  td: ({ children }) => (
    <td
      style={{
        border: "1px solid var(--hair)",
        padding: "7px 10px",
        verticalAlign: "top",
      }}
    >
      {children}
    </td>
  ),
};

export function AgentMarkdown({ source }: { source: string }) {
  return (
    <ReactMarkdown remarkPlugins={[remarkGfm]} components={COMPONENTS}>
      {source}
    </ReactMarkdown>
  );
}
