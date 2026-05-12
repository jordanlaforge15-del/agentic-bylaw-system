// Animated card for the home hero. Rotates through three sample addresses,
// typing the question 32ms/char, pausing into a "reading" state for 1.8s,
// then fading in a verdict + citation chips for 3.2s before advancing to
// the next sample.

"use client";

import { useEffect, useState } from "react";
import { HighlightWord } from "@/components/highlight-word";
import { Mono } from "@/components/mono";
import { SAMPLE_READINGS } from "@/lib/mock";

type Phase = "typing" | "reading" | "answer";

export function AgentWalkthrough() {
  const [idx, setIdx] = useState(0);
  const [phase, setPhase] = useState<Phase>("typing");
  const [typed, setTyped] = useState("");
  const sample = SAMPLE_READINGS[idx];

  useEffect(() => {
    setTyped("");
    setPhase("typing");
    let i = 0;
    const iv = setInterval(() => {
      i += 1;
      setTyped(sample.q.slice(0, i));
      if (i >= sample.q.length) {
        clearInterval(iv);
        setTimeout(() => setPhase("reading"), 400);
      }
    }, 32);
    return () => clearInterval(iv);
  }, [sample.q]);

  useEffect(() => {
    if (phase === "reading") {
      const id = setTimeout(() => setPhase("answer"), 1800);
      return () => clearTimeout(id);
    }
    if (phase === "answer") {
      const id = setTimeout(
        () => setIdx((i) => (i + 1) % SAMPLE_READINGS.length),
        3200,
      );
      return () => clearTimeout(id);
    }
  }, [phase]);

  return (
    <div className="bg-surface-alt border border-hair flex flex-col overflow-hidden min-h-[320px] sm:min-h-[380px] lg:min-h-[420px]">
      <div className="px-4 sm:px-4.5 py-2.5 sm:py-3 border-b border-hair flex items-center justify-between gap-2">
        <div className="flex items-center gap-2 min-w-0">
          <span
            className="abs-pulse-dot bg-accent flex-shrink-0"
            style={{ width: 7, height: 7 }}
          />
          <Mono muted>ABS AGENT · LIVE</Mono>
        </div>
        <Mono muted className="truncate">
          {sample.addr.toUpperCase()} · {sample.zone}
        </Mono>
      </div>

      <div className="flex-1 px-4 sm:px-5 lg:px-[22px] py-4 sm:py-5 flex flex-col gap-3 sm:gap-3.5">
        <div
          className="self-end bg-text text-surface px-3 sm:px-3.5 py-2.5 text-[13px] sm:text-[14px]"
          style={{ maxWidth: "82%" }}
        >
          {typed}
          {phase === "typing" && <span className="abs-cursor">▍</span>}
        </div>

        {(phase === "reading" || phase === "answer") && (
          <div
            className="self-start flex flex-col gap-2 sm:gap-2.5"
            style={{ maxWidth: "92%" }}
          >
            <div className="flex items-center gap-2.5 font-mono text-[11px] sm:text-[11.5px] text-text-muted tracking-[0.02em]">
              <span className="text-accent-ink">→</span>
              <span>Reading HRM LUB {sample.cite}…</span>
              {phase === "reading" && (
                <span
                  className="abs-pulse-dot bg-accent"
                  style={{ width: 5, height: 5 }}
                />
              )}
            </div>
            {phase === "answer" && (
              <div className="flex flex-col gap-2 sm:gap-2.5">
                <div className="text-[13px] sm:text-[13.5px] leading-[1.5] text-text">
                  Here&apos;s what the bylaw says:
                </div>
                <div
                  className="abs-fade-in font-sans font-extrabold text-[22px] sm:text-[24px] lg:text-[28px] leading-[1.1]"
                  style={{ letterSpacing: "-0.035em" }}
                >
                  <HighlightWord>{sample.verdict}</HighlightWord>
                </div>
                <div className="abs-fade-in flex gap-2 flex-wrap mt-1">
                  <Chip>SOURCE · {sample.cite}</Chip>
                  <Chip>0.93 CONF.</Chip>
                  <Chip>VERIFIED 2026·04·30</Chip>
                </div>
              </div>
            )}
          </div>
        )}
      </div>

      <div className="px-4 sm:px-4.5 py-2.5 sm:py-3 border-t border-hair flex justify-between items-center">
        <div className="flex gap-1">
          {SAMPLE_READINGS.map((_, i) => (
            <span
              key={i}
              className="transition-colors duration-300"
              style={{
                width: 14,
                height: 2,
                background:
                  i === idx ? "var(--accent)" : "var(--hair)",
              }}
            />
          ))}
        </div>
        <Mono muted size={9.5}>
          SAMPLE {idx + 1} / {SAMPLE_READINGS.length}
        </Mono>
      </div>
    </div>
  );
}

function Chip({ children }: { children: React.ReactNode }) {
  return (
    <span
      className="font-mono text-text-muted border border-hair"
      style={{
        fontSize: 10,
        padding: "3px 8px",
        letterSpacing: "0.06em",
      }}
    >
      {children}
    </span>
  );
}
