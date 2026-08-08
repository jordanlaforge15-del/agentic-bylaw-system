// ABS-451: one clause-detail surface for the whole workspace.
//
// Before this, only the right rail's "CITED THIS THREAD" cards could
// open a clause (each ParcelPane owned its own drawer state). The same
// citations rendered inline in agent prose and table cells were inert
// text. This provider hoists the drawer to the workspace root and hands
// every renderer — rail cards and inline references alike — the same
// `open()` and the same citation index, so a citation behaves the same
// way wherever the reader happens to be looking.
//
// Consumers call `useCitationViewer()`, which returns `null` outside a
// provider (e.g. the print surface, the standalone answer page). Those
// callers fall back to plain text / local state rather than crashing.

"use client";

import {
  createContext,
  useCallback,
  useContext,
  useMemo,
  useState,
  type ReactNode,
} from "react";
import { CitationDrawer } from "@/components/product/citation-drawer";
import {
  buildCitationIndex,
  type CitationIndex,
  type CitationRef,
} from "@/lib/citations";

type CitationViewer = {
  /** Label → citation lookup for the current thread's retrieved clauses. */
  index: CitationIndex;
  /** Open the clause-detail drawer on a citation. */
  open: (ref: CitationRef) => void;
};

const CitationViewerContext = createContext<CitationViewer | null>(null);

export function useCitationViewer(): CitationViewer | null {
  return useContext(CitationViewerContext);
}

export function CitationViewerProvider({
  citations,
  children,
}: {
  citations: readonly CitationRef[];
  children: ReactNode;
}) {
  const [active, setActive] = useState<CitationRef | null>(null);
  const index = useMemo(() => buildCitationIndex(citations), [citations]);
  const open = useCallback((ref: CitationRef) => setActive(ref), []);
  const value = useMemo<CitationViewer>(
    () => ({ index, open }),
    [index, open],
  );

  return (
    <CitationViewerContext.Provider value={value}>
      {children}
      {active && (
        <CitationDrawer
          citation={active.citation}
          title={active.title}
          onClose={() => setActive(null)}
        />
      )}
    </CitationViewerContext.Provider>
  );
}
