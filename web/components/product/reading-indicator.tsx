// Animated "reading the bylaw" cursor — the agent's thinking indicator.
//
// Shared between the /app chat thread (Conversation product) and the
// /app/answers post-purchase surface (Answers product) so both deliver
// the same animated "ABS · READING → Reading the bylaw…" cursor while the
// engine grounds an answer. Extracted from chat-thread.tsx (ABS-331) so
// the answer view stops showing a static "Generating your answer" state
// and reinstates the app-page reading UX.
//
// Markup is preserved verbatim from the original ChatThread `ThinkingMsg`
// so the ABS-25 thinking-indicator e2e (the "ABS · READING" badge + the
// `.abs-ellipsis-dot` spans) keeps passing.

import { Mono } from "@/components/mono";

export function AnimatedEllipsis() {
  return (
    <span aria-hidden="true">
      <span className="abs-ellipsis-dot">.</span>
      <span className="abs-ellipsis-dot">.</span>
      <span className="abs-ellipsis-dot">.</span>
    </span>
  );
}

// The agent "reading" row: avatar tile, "ABS · READING" badge, pulse dot,
// and the animated label. `label` may carry a trailing ellipsis/period —
// it is stripped before the animated dots are appended so the animation
// never doubles up the "…".
export function ReadingIndicator({ label }: { label: string }) {
  const labelText = label.replace(/[.…]+$/, "");
  return (
    <div className="flex flex-col gap-2.5 sm:gap-3 mb-1">
      <div className="flex items-center gap-2">
        <div
          className="bg-accent flex items-center justify-center"
          style={{ width: 22, height: 22 }}
        >
          <span
            className="font-sans font-extrabold text-on-accent"
            style={{ fontSize: 11, letterSpacing: "-0.04em" }}
          >
            a
          </span>
        </div>
        <Mono muted>ABS · READING</Mono>
        <span
          className="abs-pulse-dot bg-accent"
          style={{ width: 6, height: 6 }}
        />
      </div>
      <div className="pl-7 sm:pl-8">
        <div
          className="flex items-center gap-2.5 font-mono text-[11.5px] sm:text-[12px]"
          style={{ letterSpacing: "0.02em" }}
        >
          <span className="text-accent-ink">→</span>
          <span>
            {labelText}
            <AnimatedEllipsis />
          </span>
        </div>
      </div>
    </div>
  );
}
