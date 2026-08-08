// ABS-451: make "(Section 442)" inside agent prose and table cells open
// the same clause-detail drawer as the right rail's citation cards.
//
// `CitationText` wraps the children of every text-bearing markdown
// element (see agent-markdown.tsx). It only ever rewrites *string*
// children — element children are handled by their own overrides, which
// is how code spans, links, and other non-prose stay untouched.
//
// A reference is only rendered as a control when it resolves against the
// thread's citation index. Unresolved text ("Section 999" the agent
// never actually retrieved) stays exactly as written: inert, unstyled,
// and honest about it.

"use client";

import { Children, type ReactNode } from "react";
import { useCitationViewer } from "@/components/product/citation-viewer";
import { splitCitations, type CitationRef } from "@/lib/citations";

export function CitationText({ children }: { children: ReactNode }) {
  const viewer = useCitationViewer();
  if (!viewer || viewer.index.count === 0) return <>{children}</>;

  const out: ReactNode[] = [];
  Children.toArray(children).forEach((child, childIdx) => {
    if (typeof child !== "string") {
      out.push(child);
      return;
    }
    for (const [partIdx, part] of splitCitations(
      child,
      viewer.index,
    ).entries()) {
      if (part.kind === "text") {
        out.push(part.text);
        continue;
      }
      out.push(
        <InlineCitation
          key={`c${childIdx}-${partIdx}`}
          label={part.text}
          citation={part.ref}
          onOpen={viewer.open}
        />,
      );
    }
  });

  return <>{out}</>;
}

function InlineCitation({
  label,
  citation,
  onOpen,
}: {
  label: string;
  citation: CitationRef;
  onOpen: (ref: CitationRef) => void;
}) {
  return (
    <button
      type="button"
      data-testid="inline-citation"
      data-citation={citation.citation}
      // Distinct from the rail card's "View clause: …" so the two
      // affordances stay separately addressable in tests and to AT.
      aria-label={`View cited clause ${label}`}
      title={citation.title}
      onClick={() => onOpen(citation)}
      className={
        "inline bg-transparent p-0 m-0 border-0 text-left cursor-pointer " +
        "text-accent-ink underline decoration-dotted underline-offset-2 " +
        "hover:decoration-solid focus-visible:outline-none " +
        "focus-visible:ring-1 focus-visible:ring-accent"
      }
      style={{
        font: "inherit",
        letterSpacing: "inherit",
        lineHeight: "inherit",
      }}
    >
      {label}
    </button>
  );
}
