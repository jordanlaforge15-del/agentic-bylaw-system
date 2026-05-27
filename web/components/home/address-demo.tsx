// Home page "Try It" demo. Only the three pre-recorded SAMPLE_READINGS are
// offered — no free-text input. This prevents the widget from echoing
// arbitrary or nonsense addresses with a result that looks live.

"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";
import { HighlightWord } from "@/components/highlight-word";
import { Mono } from "@/components/mono";
import { Btn } from "@/components/btn";
import { SAMPLE_READINGS, type SampleReading } from "@/lib/mock";

const STEPS = [
  "Geocoding parcel…",
  "Fetching HRM Land Use By-law…",
  "Identifying applicable zone standards…",
  "Cross-checking dimensional requirements…",
  "Compiling answer…",
];

type State = "idle" | "thinking" | "done";

type ResolvedReading = SampleReading & { addr: string };

export function AddressDemo() {
  const router = useRouter();
  const [state, setState] = useState<State>("idle");
  const [reading, setReading] = useState<ResolvedReading | null>(null);
  const [step, setStep] = useState(0);

  const submit = (presetAddr: string) => {
    const r = SAMPLE_READINGS.find((s) => s.addr === presetAddr) ?? SAMPLE_READINGS[0];
    setState("thinking");
    setStep(0);
    let i = 0;
    const tick = () => {
      i += 1;
      if (i >= STEPS.length) {
        setReading({ ...r, addr: presetAddr });
        setState("done");
      } else {
        setStep(i);
        setTimeout(tick, 480 + Math.random() * 200);
      }
    };
    setTimeout(tick, 380);
  };

  const reset = () => {
    setState("idle");
    setReading(null);
    setStep(0);
  };

  return (
    <div
      className="bg-surface-alt p-4 sm:p-5 lg:p-[22px] flex flex-col gap-3 sm:gap-3.5 min-h-[280px] sm:min-h-[320px]"
      style={{ border: "1.5px solid var(--text)" }}
    >
      <div className="flex items-center justify-between">
        <Mono muted>TRY IT · RECORDED SAMPLES</Mono>
        {state !== "idle" && (
          <button
            onClick={reset}
            className="bg-transparent text-text-muted cursor-pointer font-mono"
            style={{ fontSize: 10, letterSpacing: "0.12em" }}
          >
            RESET ↻
          </button>
        )}
      </div>

      {state === "idle" && (
        <div className="flex flex-col gap-2.5">
          <p className="text-[12px] text-text-muted font-mono tracking-[0.03em]">
            SELECT A SAMPLE ADDRESS TO SEE A RECORDED READING:
          </p>
          <div className="flex gap-2 flex-wrap">
            {SAMPLE_READINGS.map((s) => (
              <button
                key={s.addr}
                onClick={() => submit(s.addr)}
                className="bg-transparent border border-hair text-text font-mono cursor-pointer hover:bg-surface transition-colors"
                style={{
                  fontSize: 11,
                  letterSpacing: "0.04em",
                  padding: "7px 12px",
                }}
              >
                {s.addr}
              </button>
            ))}
          </div>
        </div>
      )}

      {state === "thinking" && (
        <div className="flex-1 flex flex-col gap-2 pt-1">
          <Mono accent size={11}>
            READING · {Math.round((step / STEPS.length) * 100)}%
          </Mono>
          <div className="flex flex-col gap-1.5">
            {STEPS.slice(0, step + 1).map((s, i) => (
              <div
                key={i}
                className="flex items-center gap-2.5 font-mono text-[12px] tracking-[0.02em]"
                style={{
                  color: i === step ? "var(--text)" : "var(--text-muted)",
                }}
              >
                <span className="text-accent-ink">
                  {i === step ? "→" : "✓"}
                </span>
                <span>{s}</span>
                {i === step && (
                  <span
                    className="abs-pulse-dot bg-accent ml-1"
                    style={{ width: 6, height: 6 }}
                  />
                )}
              </div>
            ))}
          </div>
          <div className="flex-1" />
          <div className="relative h-[3px] bg-hair overflow-hidden">
            <div
              className="absolute left-0 top-0 bottom-0 bg-accent transition-[width] duration-[400ms] ease"
              style={{ width: `${((step + 1) / STEPS.length) * 100}%` }}
            />
          </div>
        </div>
      )}

      {state === "done" && reading && (
        <div className="flex flex-col gap-3.5 pt-1">
          <div className="flex justify-between items-baseline">
            <Mono muted>
              {reading.addr.toUpperCase()} · {reading.zone}
            </Mono>
            <Mono muted>SAMPLE READING</Mono>
          </div>
          <div className="text-[14px] text-text-muted italic">
            {reading.q}
          </div>
          <div
            className="font-sans font-extrabold text-[24px] sm:text-[28px] lg:text-[32px] leading-[1.1]"
            style={{ letterSpacing: "-0.035em" }}
          >
            <HighlightWord>{reading.verdict}</HighlightWord>
          </div>
          <div className="flex flex-col sm:flex-row sm:justify-between sm:items-center gap-2.5 sm:gap-2 pt-2 border-t border-hair">
            <Mono muted>SOURCE · HRM LUB {reading.cite}</Mono>
            <Btn
              variant="ghost"
              size="sm"
              className="self-start sm:self-auto"
              onClick={() => {
                const params = new URLSearchParams({
                  anchor_label: reading.addr,
                  first_message: reading.q,
                });
                router.push(`/cases/new?${params.toString()}`);
              }}
            >
              Open full reading →
            </Btn>
          </div>
        </div>
      )}
    </div>
  );
}
