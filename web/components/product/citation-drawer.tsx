// Clause-detail drawer. Fetches /api/citation for a single citation
// path and renders the outline path, clause text, and source page.
//
// Extracted from parcel-pane.tsx (ABS-451) so the two places a citation
// can be clicked — the right rail's "CITED THIS THREAD" cards and the
// inline references inside agent prose / table cells — open the exact
// same surface instead of drifting apart.

"use client";

import { useEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { Mono } from "@/components/mono";

type CitationDetail = {
  citation_path?: string;
  citation_label?: string;
  bylaw_name?: string;
  text?: string;
  page_start?: number;
  page_end?: number;
  ancestor_chain?: Array<{
    citation_path?: string;
    citation_label?: string;
    text_excerpt?: string;
  }>;
};

export function CitationDrawer({
  citation,
  title,
  onClose,
}: {
  citation: string;
  title: string;
  onClose: () => void;
}) {
  const [detail, setDetail] = useState<CitationDetail | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [mounted, setMounted] = useState(false);
  const closeRef = useRef<HTMLButtonElement>(null);

  useEffect(() => setMounted(true), []);

  useEffect(() => {
    closeRef.current?.focus();
  }, []);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  useEffect(() => {
    let cancelled = false;
    setDetail(null);
    setError(null);

    fetch(`/api/citation?citation_path=${encodeURIComponent(citation)}`)
      .then((r) => {
        if (!r.ok) throw new Error(`${r.status}`);
        return r.json();
      })
      .then((d: CitationDetail) => {
        if (!cancelled) setDetail(d);
      })
      .catch((e: Error) => {
        if (!cancelled) setError(e.message);
      });

    return () => {
      cancelled = true;
    };
  }, [citation]);

  if (!mounted) return null;

  return createPortal(
    <div
      className="fixed inset-0 z-50 flex items-stretch justify-end"
      aria-modal="true"
      role="dialog"
      aria-label="Clause detail"
    >
      {/* Scrim */}
      <button
        type="button"
        aria-label="Close"
        onClick={onClose}
        className="absolute inset-0 bg-overlay cursor-default"
      />

      {/* Drawer panel — slides in from right */}
      <div
        className="relative bg-surface border-l border-hair shadow-lg flex flex-col overflow-hidden"
        style={{ width: "min(480px, 100vw)", maxHeight: "100dvh" }}
      >
        {/* Header */}
        <div className="border-b border-hair px-5 py-4 flex justify-between items-center gap-3 flex-shrink-0">
          <div className="flex flex-col gap-0.5 min-w-0">
            <Mono accent size={11} className="font-semibold truncate">
              {compactCitation(citation)}
            </Mono>
            <span className="text-[11px] text-text-muted truncate">{title}</span>
          </div>
          <button
            ref={closeRef}
            type="button"
            aria-label="Close clause detail"
            onClick={onClose}
            className="text-text-muted hover:text-text transition-colors text-[18px] leading-none flex-shrink-0"
          >
            ×
          </button>
        </div>

        {/* Body */}
        <div className="flex-1 overflow-y-auto px-5 py-4 flex flex-col gap-5">
          {!detail && !error && (
            <div className="text-[12.5px] text-text-muted">Loading…</div>
          )}

          {error && (
            <div className="text-[12.5px] text-text-muted">
              Could not load clause text ({error}).
            </div>
          )}

          {detail && (
            <>
              {/* Breadcrumb path */}
              {detail.ancestor_chain && detail.ancestor_chain.length > 0 && (
                <div className="flex flex-col gap-1">
                  <Mono muted>OUTLINE PATH</Mono>
                  <div className="flex flex-wrap gap-1 items-center">
                    {detail.ancestor_chain.map((a, i) => (
                      <span key={i} className="flex items-center gap-1">
                        {i > 0 && (
                          <span className="text-text-muted text-[10px]">›</span>
                        )}
                        <span className="text-[11px] font-mono text-text-muted">
                          {a.citation_label || a.citation_path}
                        </span>
                      </span>
                    ))}
                    <span className="flex items-center gap-1">
                      <span className="text-text-muted text-[10px]">›</span>
                      <Mono accent size={11} className="font-semibold">
                        {detail.citation_label || compactCitation(citation)}
                      </Mono>
                    </span>
                  </div>
                </div>
              )}

              {/* Clause text */}
              {detail.text && (
                <div className="flex flex-col gap-1.5">
                  <Mono muted>CLAUSE TEXT</Mono>
                  <p
                    className="text-[13px] leading-[1.65] text-text m-0 whitespace-pre-wrap"
                    style={{ fontFamily: "inherit" }}
                  >
                    {detail.text}
                  </p>
                </div>
              )}

              {/* Page reference */}
              {detail.page_start != null && (
                <div className="flex flex-col gap-1">
                  <Mono muted>SOURCE</Mono>
                  <span className="text-[12px] text-text-muted">
                    {detail.bylaw_name}
                    {detail.page_start != null &&
                      ` · p. ${detail.page_start}${detail.page_end && detail.page_end !== detail.page_start ? `–${detail.page_end}` : ""}`}
                  </span>
                </div>
              )}
            </>
          )}
        </div>
      </div>
    </div>,
    document.body,
  );
}

// "Schedule 17 > 117 > [Maximum Streetwall Heights] > (a)" → "§ 117(a)"-ish.
// The full path is too noisy for a card; we keep the most distinctive
// segment (the leaf label) plus an optional schedule prefix.
export function compactCitation(path: string): string {
  const parts = path.split(/\s*>\s*/);
  if (parts.length === 1) return path;
  const lead = parts[0];
  const tail = parts[parts.length - 1];
  if (lead.toLowerCase().startsWith("schedule")) {
    return `${lead} · ${tail}`;
  }
  return tail;
}
