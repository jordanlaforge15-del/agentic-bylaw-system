// Right pane of /app. Shows the parcel context derived from the
// current session's spatial-join tool results: address, geocode
// confidence, zone / height / heritage / FAR / bonus / shadow rows
// (only the ones actually returned by the spatial query — empty
// datasets are dropped), and a "cited this thread" list of distinct
// citations the agent has pulled.
//
// When `parcel` is null, we render an honest empty state rather than
// stale fixtures. The site-plan SVG stays as a schematic placeholder
// for now; eventually it would be drawn from the resolved parcel
// polygon, but that needs the geocoder to surface the parcel
// geometry first.

"use client";

import { useEffect, useState } from "react";
import { createPortal } from "react-dom";
import { Btn } from "@/components/btn";
import { Mono } from "@/components/mono";
import {
  CitationDrawer,
  compactCitation,
} from "@/components/product/citation-drawer";
import { useCitationViewer } from "@/components/product/citation-viewer";
import type { CitationRef } from "@/lib/citations";
import type { ParcelContext } from "@/lib/parcel";

type Props = {
  parcel: ParcelContext | null;
  sessionId?: string | null;
  caseId?: number | null;
  // When the case has an address anchor but no spatial lookup has run
  // yet, show the anchor address in the empty state instead of the
  // generic "No parcel yet" copy.
  anchorLabel?: string | null;
  anchorKind?: string | null;
  // ABS-423: terminal status of the case-open spatial join
  // (`spatial_facts.status`). When it is "unresolved" the server has
  // already tried and failed, so the empty state must say so rather
  // than keep promising a pending geocode.
  spatialStatus?: string | null;
  spatialReason?: string | null;
  // When `true`, the pane drops its fixed width and left border — the
  // parent (Sheet on mobile, side overlay on tablet) supplies them.
  inSheet?: boolean;
  // ABS-346: when the workspace has a primary report deliverable, this pane's
  // export is demoted to the SECONDARY "sources appendix" (the follow-up
  // conversation + citations). One clear hierarchy: report primary, sources
  // secondary. On a conversation-only case it stays the primary export.
  appendix?: boolean;
};

export function ParcelPane({
  parcel,
  sessionId,
  caseId,
  anchorLabel,
  anchorKind,
  spatialStatus,
  spatialReason,
  inSheet,
  appendix,
}: Props) {
  const [shareOpen, setShareOpen] = useState(false);
  // ABS-451: inside the workspace the clause drawer is owned by the
  // CitationViewerProvider, so a rail card and an inline reference open
  // the same panel. Standalone mounts (no provider) keep the original
  // self-contained drawer.
  const viewer = useCitationViewer();
  const [localCitation, setLocalCitation] = useState<CitationRef | null>(null);
  const openCitation = viewer ? viewer.open : setLocalCitation;

  const handleExport = () => {
    if (!sessionId) return;
    window.open(`/app/print?session_id=${encodeURIComponent(sessionId)}`, "_blank");
  };

  return (
    <aside
      className={
        inSheet
          ? "bg-surface-alt flex flex-col min-h-0 overflow-auto h-full w-full"
          : "border-l border-hair bg-surface-alt flex flex-col min-h-0 overflow-auto w-[340px] flex-shrink-0"
      }
    >
      <div className="border-b border-hair px-5 py-4 flex justify-between items-center">
        <Mono muted>PARCEL</Mono>
        {parcel && (
          <Mono muted size={9.5}>
            {parcel.geocode
              ? `${parcel.geocode.resolver?.toUpperCase() || "GEOCODED"} · ${(parcel.geocode.confidence * 100).toFixed(0)}%`
              : "—"}
          </Mono>
        )}
      </div>

      {parcel ? (
        <ParcelDetails parcel={parcel} onCitationClick={openCitation} />
      ) : (
        <EmptyParcel
          anchorLabel={anchorLabel}
          anchorKind={anchorKind}
          spatialStatus={spatialStatus}
          spatialReason={spatialReason}
        />
      )}

      <div className="mt-auto border-t border-hair px-5 py-3.5 flex flex-col gap-2">
        {appendix && (
          <Mono muted size={9} className="tracking-[0.1em]">
            SOURCES · SECONDARY TO THE REPORT
          </Mono>
        )}
        <Btn
          // Demoted to ghost when a report is the primary deliverable, so the
          // report's own "Export PDF" (case toolbar) reads as primary.
          variant={appendix ? "ghost" : "primary"}
          size="sm"
          className="w-full"
          disabled={!sessionId}
          style={{ opacity: sessionId ? 1 : 0.5 }}
          onClick={handleExport}
          data-testid="parcel-export"
        >
          {appendix ? "Export sources (appendix)" : "Export reading (PDF)"}
        </Btn>
        <Btn
          variant="ghost"
          size="sm"
          className="w-full"
          disabled={!caseId}
          style={{ opacity: caseId ? 1 : 0.5 }}
          onClick={() => caseId && setShareOpen(true)}
        >
          Share with team
        </Btn>
      </div>

      {shareOpen && caseId && (
        <ShareModal
          caseId={caseId}
          onClose={() => setShareOpen(false)}
        />
      )}

      {localCitation && !viewer && (
        <CitationDrawer
          citation={localCitation.citation}
          title={localCitation.title}
          onClose={() => setLocalCitation(null)}
        />
      )}
    </aside>
  );
}

// ── Share modal ─────────────────────────────────────────────────────────────

type ShareModalProps = {
  caseId: number;
  onClose: () => void;
};

function ShareModal({ caseId, onClose }: ShareModalProps) {
  const [copied, setCopied] = useState(false);
  const [mounted, setMounted] = useState(false);

  useEffect(() => setMounted(true), []);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  const shareUrl =
    typeof window !== "undefined"
      ? `${window.location.origin}/app?case_id=${caseId}`
      : "";

  const handleCopy = async () => {
    try {
      await navigator.clipboard.writeText(shareUrl);
    } catch {
      // Fallback for non-HTTPS or restricted contexts
      const el = document.createElement("textarea");
      el.value = shareUrl;
      el.style.position = "fixed";
      el.style.opacity = "0";
      document.body.appendChild(el);
      el.select();
      document.execCommand("copy");
      document.body.removeChild(el);
    }
    setCopied(true);
    setTimeout(() => setCopied(false), 2000);
  };

  if (!mounted) return null;

  return createPortal(
    <div
      className="fixed inset-0 z-50 flex items-center justify-center"
      aria-modal="true"
      role="dialog"
      aria-label="Share this reading"
    >
      {/* Scrim */}
      <button
        type="button"
        aria-label="Close"
        onClick={onClose}
        className="absolute inset-0 bg-overlay cursor-default"
      />

      {/* Panel */}
      <div
        className="relative bg-surface border border-hair shadow-lg w-full mx-4"
        style={{ maxWidth: 400 }}
      >
        {/* Header */}
        <div className="border-b border-hair px-5 py-4 flex justify-between items-center">
          <Mono>SHARE READING</Mono>
          <button
            type="button"
            aria-label="Close share modal"
            onClick={onClose}
            className="text-text-muted hover:text-text transition-colors text-[18px] leading-none"
          >
            ×
          </button>
        </div>

        {/* Body */}
        <div className="px-5 py-5 flex flex-col gap-4">
          {/* Link copy */}
          <div className="flex flex-col gap-2">
            <label className="text-[11px] font-mono text-text-muted tracking-[0.05em] uppercase">
              Link
            </label>
            <div className="flex gap-2">
              <input
                type="text"
                readOnly
                value={shareUrl}
                className="flex-1 bg-surface-alt border border-hair px-3 py-2 text-[12.5px] font-mono text-text-muted min-w-0"
                onFocus={(e) => e.currentTarget.select()}
              />
              <Btn
                variant="primary"
                size="sm"
                onClick={handleCopy}
                style={{ whiteSpace: "nowrap" }}
              >
                {copied ? "Copied!" : "Copy link"}
              </Btn>
            </div>
            <p className="text-[11.5px] text-text-muted leading-[1.5] m-0">
              Anyone with access to this account can open this case from the link.
            </p>
          </div>

          {/* Email invite — coming soon */}
          <div
            className="border border-hair px-4 py-3 flex flex-col gap-1"
            style={{ opacity: 0.6 }}
          >
            <span className="text-[11px] font-mono tracking-[0.05em] uppercase text-text-muted">
              Invite by email
            </span>
            <span className="text-[12.5px] text-text-muted">
              Coming in beta — team email invites and shared workspaces are on the roadmap.
            </span>
          </div>
        </div>
      </div>
    </div>,
    document.body,
  );
}

// ── Parcel detail ────────────────────────────────────────────────────────────

// ABS-423: the extractor's `reason` strings are written for the model's
// system prompt, not for a user. Translate the ones we ship; anything
// unmapped falls through verbatim so a new failure mode is still legible
// (and debuggable) rather than silently swallowed.
const UNRESOLVED_REASONS: Record<string, string> = {
  "could not parse anchor as a civic address or PID":
    "we couldn't read this anchor as a civic address or PID",
  "geocoder could not resolve anchor":
    "the address didn't match any known Halifax location",
  "geocoded point is not inside any parcel polygon":
    "the geocoded point doesn't fall inside any mapped parcel",
  "anchor_kind is not 'address'":
    "this case is anchored to a project reference, not a civic address",
  "resolved geometry has no usable centroid":
    "the matched location has no usable centre point",
};

function humanizeUnresolvedReason(reason?: string | null): string | null {
  if (!reason) return null;
  return UNRESOLVED_REASONS[reason] ?? reason;
}

function EmptyParcel({
  anchorLabel,
  anchorKind,
  spatialStatus,
  spatialReason,
}: {
  anchorLabel?: string | null;
  anchorKind?: string | null;
  spatialStatus?: string | null;
  spatialReason?: string | null;
}) {
  const hasAddressAnchor = anchorKind === "address" && anchorLabel;

  // Terminal failure beats the pending copy: the server already tried
  // and stored the outcome, so no amount of asking will fill this pane.
  if (spatialStatus === "unresolved" && anchorLabel) {
    const reason = humanizeUnresolvedReason(spatialReason);
    return (
      <div className="px-5 py-[18px] flex flex-col gap-1.5">
        <div
          className="font-sans font-bold leading-[1.15]"
          style={{ fontSize: 22, letterSpacing: "-0.025em" }}
          data-testid="parcel-anchor-address"
        >
          {anchorLabel}
        </div>
        <div className="text-[12.5px] text-text-muted">
          Halifax Regional Municipality
        </div>
        <p
          className="text-[12.5px] text-text-muted leading-[1.55] m-0 mt-2"
          data-testid="parcel-unresolved"
        >
          We couldn&rsquo;t locate this address in the parcel data
          {reason ? ` — ${reason}` : ""}. Bylaw answers still work, but
          parcel attributes (zone, height, FAR) won&rsquo;t appear here.
          Open a new case with a corrected civic address to try again.
        </p>
      </div>
    );
  }

  if (hasAddressAnchor) {
    return (
      <div className="px-5 py-[18px] flex flex-col gap-1.5">
        <div
          className="font-sans font-bold leading-[1.15]"
          style={{ fontSize: 22, letterSpacing: "-0.025em" }}
          data-testid="parcel-anchor-address"
        >
          {anchorLabel}
        </div>
        <div className="text-[12.5px] text-text-muted">
          Halifax Regional Municipality
        </div>
        <p className="text-[12.5px] text-text-muted leading-[1.55] m-0 mt-2">
          Geocoding pending — ask a bylaw question about this address and
          the spatial-join attributes will appear here.
        </p>
      </div>
    );
  }

  return (
    <div className="px-5 py-7 flex flex-col gap-3">
      <div
        className="font-sans font-bold leading-[1.15]"
        style={{ fontSize: 18, letterSpacing: "-0.02em" }}
      >
        No parcel yet.
      </div>
      <p className="text-[13px] text-text-muted leading-[1.55] m-0">
        Ask a question with a Halifax civic address — e.g.{" "}
        <em>&ldquo;What zone is 1967 Woodlawn Terrace?&rdquo;</em> — and the
        spatial-join attributes will land here: zone, max height, heritage
        district, FAR, bonus zoning, shadow-impact overlap.
      </p>
    </div>
  );
}

function ParcelDetails({
  parcel,
  onCitationClick,
}: {
  parcel: ParcelContext;
  onCitationClick: (c: CitationRef) => void;
}) {
  const rows = buildRows(parcel);
  return (
    <>
      <div className="px-5 py-[18px] flex flex-col gap-1.5">
        <div
          className="font-sans font-bold leading-[1.15]"
          style={{ fontSize: 22, letterSpacing: "-0.025em" }}
        >
          {parcel.address.civic_number} {parcel.address.street}
        </div>
        <div className="text-[12.5px] text-text-muted">
          Halifax Regional Municipality
        </div>
      </div>

      <div className="px-5 pb-[18px] flex flex-col gap-2">
        {rows.map(([k, v]) => (
          <div
            key={k}
            className="flex justify-between gap-3 text-[12px] pb-1.5"
            style={{ borderBottom: "1px dotted var(--hair)" }}
          >
            <span
              className="text-text-muted font-mono shrink-0"
              style={{ letterSpacing: "0.04em" }}
            >
              {k}
            </span>
            <span
              className="font-semibold text-right"
              style={{ wordBreak: "break-word" }}
            >
              {v}
            </span>
          </div>
        ))}
      </div>

      {parcel.cited.length > 0 && (
        <div className="border-t border-hair px-5 py-3 flex flex-col gap-2.5">
          <Mono muted>CITED THIS THREAD · {parcel.cited.length}</Mono>
          {parcel.cited.map((s) => (
            <button
              key={s.citation}
              type="button"
              onClick={() => onCitationClick(s)}
              className="bg-surface border border-hair p-3 flex flex-col gap-1 text-left w-full transition-colors hover:border-accent hover:bg-surface-alt focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-accent"
              aria-label={`View clause: ${s.title}`}
            >
              <div className="flex justify-between items-baseline gap-2">
                <Mono accent size={11} className="font-semibold">
                  {compactCitation(s.citation)}
                </Mono>
                {s.date && (
                  <Mono muted size={9}>
                    {s.date}
                  </Mono>
                )}
              </div>
              <span className="text-[12.5px] text-text-muted">{s.title}</span>
            </button>
          ))}
        </div>
      )}
    </>
  );
}

function buildRows(parcel: ParcelContext): Array<[string, string]> {
  const rows: Array<[string, string]> = [];
  if (parcel.zone) {
    rows.push([
      "Zone",
      parcel.zone.description
        ? `${parcel.zone.code} · ${parcel.zone.description}`
        : parcel.zone.code,
    ]);
  }
  if (parcel.height && parcel.height.max_m != null) {
    rows.push(["Max height", `${parcel.height.max_m} m`]);
  }
  if (parcel.heritage) {
    rows.push([
      "Heritage",
      parcel.heritage.status
        ? `${parcel.heritage.name} (${parcel.heritage.status})`
        : parcel.heritage.name,
    ]);
  }
  if (parcel.far && parcel.far.max != null) {
    rows.push(["Max FAR", parcel.far.max.toString()]);
  }
  if (parcel.bonus) {
    rows.push(["Bonus zoning", parcel.bonus.name]);
  }
  if (parcel.shadow) {
    rows.push(["Shadow impact", parcel.shadow.area]);
  }
  if (rows.length === 0) {
    rows.push(["Spatial match", "Address geocoded but no attribute layers hit"]);
  }
  return rows;
}
