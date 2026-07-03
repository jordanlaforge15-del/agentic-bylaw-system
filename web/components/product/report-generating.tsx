// ABS-343 — the post-purchase generation view.
//
// After free-start / checkout the engine runs as a real background job
// (advisor.billing: authorized → generating → captured/voided/failed).
// While the purchase sits in `generating`, the answer-view renders THIS
// screen instead of the finished ReportDocument, then hands off to the
// document the moment the job settles.
//
// The chrome deliberately reuses the ReportDocument letterhead (accent
// rule · "ABS° · <type>" · ref) so the hand-off into the finished report
// is visually continuous — the letterhead never re-flows, only the body
// swaps. A top progress bar fills as six named assembly steps resolve in
// sequence; the active step pulses with a sub-detail line and completed
// steps tick to OK.
//
// The step cadence is a client-side animation, but the REVEAL is gated on
// the real job: the answer-view keeps this screen mounted (and the bar
// short of full) until a poll reports `captured`, then swaps to the
// document. So the finish is driven by the engine, not a fixed timer — the
// steps are just the honest "work in progress" affordance while it runs.

"use client";

import { useEffect, useState } from "react";
import { Mono } from "@/components/mono";

// The six assembly steps the spec plays (design_files/report-view.jsx →
// GEN_STEPS). `detail` is the sub-line shown while a step is active.
const GEN_STEPS: { label: string; detail: string }[] = [
  {
    label: "Resolving parcel",
    detail: "Matching the address to a parcel and its legal description.",
  },
  {
    label: "Identifying zone & overlays",
    detail: "Reading the zoning designation and any overlay districts.",
  },
  {
    label: "Retrieving provisions",
    detail: "Pulling the governing bylaw sections for this question.",
  },
  {
    label: "Evaluating against standards",
    detail: "Testing the proposal against the applicable standards.",
  },
  {
    label: "Compiling findings",
    detail: "Assembling the determination and supporting findings.",
  },
  {
    label: "Verifying citations",
    detail: "Confirming every citation resolves to the source bylaw.",
  },
];

export function ReportGenerating({
  title,
  reportType = "BYLAW REPORT",
  addressKnown = true,
}: {
  title: string;
  reportType?: string;
  addressKnown?: boolean;
}) {
  // Advance the active step on a cadence, but HOLD on the last step: the
  // final tick to "all done" is the answer-view swapping in the document
  // when the real job settles, not this timer.
  const [active, setActive] = useState(0);
  useEffect(() => {
    const t = setInterval(
      () => setActive((i) => Math.min(i + 1, GEN_STEPS.length - 1)),
      650,
    );
    return () => clearInterval(t);
  }, []);

  // Fill toward — but never quite to — 100% while running; the bar only
  // completes when this screen unmounts on `captured`.
  const pct = Math.min(96, ((active + 0.5) / GEN_STEPS.length) * 100);

  return (
    <article
      data-testid="report-generating"
      className="border border-hair bg-surface"
    >
      {/* Accent rule + progress bar — the letterhead the finished document
          shares, so the hand-off is continuous. */}
      <div className="h-1 bg-hair overflow-hidden">
        <div
          data-testid="gen-progress"
          role="progressbar"
          aria-valuemin={0}
          aria-valuemax={100}
          aria-valuenow={Math.round(pct)}
          aria-label="Generating report"
          className="h-full bg-accent transition-[width] duration-500 ease-out"
          style={{ width: `${pct}%` }}
        />
      </div>

      <div className="px-6 sm:px-9 pt-6 flex items-center justify-between gap-3 flex-wrap">
        <div className="flex items-center gap-2.5">
          <span
            className="font-sans font-extrabold text-[17px]"
            style={{ letterSpacing: "-0.03em" }}
          >
            ABS°
          </span>
          <span className="text-text-muted">·</span>
          <Mono muted size={11}>
            {reportType}
          </Mono>
        </div>
        <span className="flex items-center gap-2">
          <span
            className="abs-pulse-dot bg-accent"
            style={{ width: 6, height: 6 }}
          />
          <Mono muted size={11}>
            GENERATING
          </Mono>
        </span>
      </div>

      <div className="px-6 sm:px-9 pb-8 pt-5 flex flex-col gap-6">
        {/* Title block — mirrors the document's, so the heading holds
            steady across the hand-off. */}
        <header className="flex flex-col gap-1.5 pb-5 border-b border-hair">
          <h1
            className="font-sans font-extrabold m-0 text-[24px] sm:text-[30px] leading-[1.05]"
            style={{ letterSpacing: "-0.03em" }}
            data-testid="gen-title"
          >
            {title}
          </h1>
          <div className="text-text-muted text-[13.5px]">
            {addressKnown
              ? "Assembling your grounded bylaw report."
              : "Assembling your grounded bylaw report from the details you provided."}
          </div>
        </header>

        {/* The six steps. */}
        <ol className="flex flex-col gap-0 m-0 p-0 list-none" data-testid="gen-steps">
          {GEN_STEPS.map((step, i) => {
            const status =
              i < active ? "done" : i === active ? "active" : "pending";
            return (
              <li
                key={step.label}
                data-testid="gen-step"
                data-status={status}
                className="flex items-start gap-3 py-2.5 border-b border-hair last:border-b-0"
              >
                <StepMarker status={status} />
                <div className="flex flex-col gap-1 min-w-0">
                  <div className="flex items-center gap-2.5">
                    <span
                      className={
                        status === "pending"
                          ? "text-[14px] text-text-muted"
                          : "text-[14px]"
                      }
                    >
                      {step.label}
                    </span>
                    {status === "done" && (
                      <span
                        data-testid="gen-step-ok"
                        className="font-mono uppercase text-[10px] tracking-[0.14em] text-accent-ink"
                      >
                        OK
                      </span>
                    )}
                  </div>
                  {status === "active" && (
                    <span
                      data-testid="gen-step-detail"
                      className="text-text-muted text-[12.5px] leading-[1.5]"
                    >
                      {step.detail}
                    </span>
                  )}
                </div>
              </li>
            );
          })}
        </ol>

        {/* The "fixed & audited · saves to the case" note. */}
        <div
          data-testid="gen-note"
          className="border-t border-hair pt-5 text-text-muted text-[12px] leading-[1.55]"
        >
          This analysis is fixed and audited against a single bylaw version —
          it doesn&apos;t change between runs. The report saves to your case,
          so you can leave this page and come back to the finished document.
        </div>
      </div>
    </article>
  );
}

// The leading marker: a filled tick for done, a pulsing ring for active, a
// hollow dot for pending.
function StepMarker({ status }: { status: "done" | "active" | "pending" }) {
  if (status === "done") {
    return (
      <span
        aria-hidden="true"
        className="mt-[3px] inline-flex items-center justify-center w-[14px] h-[14px] shrink-0 bg-accent text-on-accent"
        style={{ fontSize: 9, lineHeight: 1 }}
      >
        ✓
      </span>
    );
  }
  if (status === "active") {
    return (
      <span
        aria-hidden="true"
        className="abs-pulse-dot bg-accent mt-[5px] inline-block w-[10px] h-[10px] shrink-0 rounded-full"
      />
    );
  }
  return (
    <span
      aria-hidden="true"
      className="mt-[5px] inline-block w-[10px] h-[10px] shrink-0 rounded-full border border-hair"
    />
  );
}
