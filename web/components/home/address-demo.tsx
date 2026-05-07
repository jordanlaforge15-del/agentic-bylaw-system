// Working address input on the home page. Submitting any input — typed or
// chip — runs a 5-step "thinking" sequence at 480ms each (with jitter),
// then resolves to a verdict card. Lookup matches the address against the
// known SAMPLE_READINGS by leading characters; anything else falls through
// to the first sample.

"use client";

import { useState } from "react";
import { HighlightWord } from "@/components/highlight-word";
import { Mono } from "@/components/mono";
import { Btn } from "@/components/btn";
import { SAMPLE_READINGS, type SampleReading } from "@/lib/mock";

const STEPS = [
  "Geocoding parcel…",
  "Fetching HRM Land Use By-law…",
  "Reading § 9 — Established Residential…",
  "Cross-checking § 4.3 frontage minimums…",
  "Compiling answer…",
];

type State = "idle" | "thinking" | "done";

type ResolvedReading = SampleReading & { addr: string };

export function AddressDemo() {
  const [val, setVal] = useState("");
  const [state, setState] = useState<State>("idle");
  const [reading, setReading] = useState<ResolvedReading | null>(null);
  const [step, setStep] = useState(0);

  const submit = (presetAddr?: string) => {
    const a = (presetAddr ?? val).trim();
    if (!a) return;
    setState("thinking");
    setStep(0);
    const r =
      SAMPLE_READINGS.find((s) =>
        a.toLowerCase().includes(s.addr.toLowerCase().slice(0, 5)),
      ) ?? SAMPLE_READINGS[0];
    let i = 0;
    const tick = () => {
      i += 1;
      if (i >= STEPS.length) {
        setReading({ ...r, addr: a });
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
    setVal("");
    setStep(0);
  };

  return (
    <div
      className="bg-surface-alt p-[22px] flex flex-col gap-3.5"
      style={{ border: "1.5px solid var(--text)", minHeight: 320 }}
    >
      <div className="flex items-center justify-between">
        <Mono muted>TRY IT · HRM ADDRESSES</Mono>
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
        <>
          <form
            onSubmit={(e) => {
              e.preventDefault();
              submit();
            }}
            className="flex"
            style={{ border: "1.5px solid var(--text)" }}
          >
            <input
              value={val}
              onChange={(e) => setVal(e.target.value)}
              placeholder="e.g. 5184 Morris St, Halifax"
              className="flex-1 bg-surface text-text font-sans text-[15px] outline-none px-4 py-3.5 tracking-[-0.005em]"
            />
            <button
              type="submit"
              className="bg-text text-surface font-sans font-bold px-5 cursor-pointer text-[14px] tracking-[-0.01em]"
            >
              Read it →
            </button>
          </form>
          <div className="flex gap-1.5 flex-wrap mt-1">
            <span className="text-[11.5px] text-text-muted self-center mr-1">
              or try:
            </span>
            {SAMPLE_READINGS.map((s) => (
              <button
                key={s.addr}
                onClick={() => submit(s.addr)}
                className="bg-transparent border border-hair text-text font-mono cursor-pointer"
                style={{
                  fontSize: 10.5,
                  letterSpacing: "0.04em",
                  padding: "5px 9px",
                }}
              >
                {s.addr}
              </button>
            ))}
          </div>
        </>
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
            <Mono accent>VERIFIED · 0.93 CONF</Mono>
          </div>
          <div className="text-[14px] text-text-muted italic">
            {reading.q}
          </div>
          <div
            className="font-sans font-extrabold text-[32px] leading-[1.1]"
            style={{ letterSpacing: "-0.035em" }}
          >
            <HighlightWord>{reading.verdict}</HighlightWord>
          </div>
          <div className="flex justify-between items-center pt-2 border-t border-hair">
            <Mono muted>SOURCE · HRM LUB {reading.cite}</Mono>
            <Btn variant="ghost" size="sm">
              Open full reading →
            </Btn>
          </div>
        </div>
      )}
    </div>
  );
}
