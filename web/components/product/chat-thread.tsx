// Center pane of /app. Renders system / user / agent messages and
// (optionally) a "thinking" indicator that animates step-by-step while
// the next agent reply is being fabricated. Auto-scrolls to the latest
// message on each update.

"use client";

import { useEffect, useRef, useState } from "react";
import { HighlightWord } from "@/components/highlight-word";
import { Mono } from "@/components/mono";
import type {
  AgentMessage,
  Message,
  SystemMessage,
  UserMessage,
} from "@/lib/mock";
import { cn } from "@/lib/cn";

type Props = {
  messages: Message[];
  thinking: boolean;
  thinkSteps: string[];
  thinkStep: number;
};

export function ChatThread({
  messages,
  thinking,
  thinkSteps,
  thinkStep,
}: Props) {
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (ref.current) ref.current.scrollTop = ref.current.scrollHeight;
  }, [messages.length, thinking, thinkStep]);

  return (
    <div
      ref={ref}
      className="flex-1 overflow-y-auto flex flex-col gap-[18px] px-9 py-6"
    >
      {messages.map((m, i) => {
        if (m.kind === "system") return <SystemMsg key={i} msg={m} />;
        if (m.kind === "user") return <UserMsg key={i} msg={m} />;
        return <AgentMsg key={i} msg={m} idx={i} />;
      })}
      {thinking && <ThinkingMsg steps={thinkSteps} step={thinkStep} />}
    </div>
  );
}

function SystemMsg({ msg }: { msg: SystemMessage }) {
  return (
    <div
      className="flex items-center gap-2.5 py-1.5 text-text-muted font-mono"
      style={{ fontSize: 11, letterSpacing: "0.04em" }}
    >
      <div className="flex-1 h-px bg-hair" />
      <span>{msg.body}</span>
      <div className="flex-1 h-px bg-hair" />
    </div>
  );
}

function UserMsg({ msg }: { msg: UserMessage }) {
  return (
    <div className="flex justify-end mb-1">
      <div
        className="bg-text text-surface text-[14px] leading-[1.5] px-4 py-3"
        style={{ maxWidth: "78%" }}
      >
        {msg.body}
      </div>
    </div>
  );
}

function AgentMsg({ msg, idx }: { msg: AgentMessage; idx: number }) {
  const [open, setOpen] = useState(idx === 0);
  return (
    <div className="flex flex-col gap-3 mb-1">
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
        <Mono muted>ABS · AGENT</Mono>
        <span className="flex-1" />
        <Mono accent>{(msg.confidence * 100).toFixed(0)}% CONF</Mono>
      </div>

      <div className="pl-8 flex flex-col gap-3.5">
        <div
          className="font-sans font-extrabold text-[24px] leading-[1.1]"
          style={{ letterSpacing: "-0.03em" }}
        >
          <HighlightWord>{msg.answer}</HighlightWord>
        </div>
        <div
          className="text-[14.5px] leading-[1.55] text-text"
          style={{ maxWidth: 640 }}
        >
          {msg.body}
        </div>

        <button
          type="button"
          onClick={() => setOpen((o) => !o)}
          className={cn(
            "self-start inline-flex items-center gap-2 cursor-pointer font-mono uppercase",
            "bg-transparent border border-hair text-text-muted",
            "px-2.5 py-[7px]",
          )}
          style={{ fontSize: 10.5, letterSpacing: "0.08em" }}
        >
          <span>{open ? "▾" : "▸"}</span>
          <span>{msg.reasoning.length} reasoning steps</span>
        </button>

        {open && (
          <div className="border border-hair bg-surface-alt">
            {msg.reasoning.map((r, i) => (
              <div
                key={r.n}
                className="grid items-baseline gap-3.5 px-3.5 py-3"
                style={{
                  gridTemplateColumns: "36px 76px 1fr",
                  borderBottom:
                    i < msg.reasoning.length - 1
                      ? "1px solid var(--hair)"
                      : "none",
                }}
              >
                <Mono muted>{r.n}</Mono>
                <Mono accent size={11} className="font-semibold">
                  {r.cite}
                </Mono>
                <span className="text-[13px] leading-[1.5]">{r.body}</span>
              </div>
            ))}
          </div>
        )}

        <div className="flex gap-1.5 flex-wrap">
          {msg.sources.map((s, i) => (
            <span
              key={i}
              className="inline-flex items-center gap-1.5 border border-hair font-mono text-text-muted"
              style={{
                padding: "4px 8px",
                fontSize: 10,
                letterSpacing: "0.04em",
              }}
            >
              <span className="bg-accent" style={{ width: 4, height: 4 }} />
              {s.section}
            </span>
          ))}
        </div>

        <div className="flex gap-2 mt-0.5">
          {["Copy", "Cite", "Export", "Helpful", "Off"].map((a) => (
            <button
              key={a}
              type="button"
              className="bg-transparent text-text-muted cursor-pointer font-mono uppercase"
              style={{
                padding: "4px 0",
                fontSize: 10,
                letterSpacing: "0.1em",
                marginRight: 8,
              }}
            >
              {a}
            </button>
          ))}
        </div>
      </div>
    </div>
  );
}

function ThinkingMsg({ steps, step }: { steps: string[]; step: number }) {
  return (
    <div className="flex flex-col gap-3 mb-1">
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
      <div className="pl-8 flex flex-col gap-1.5">
        {steps.slice(0, step + 1).map((s, i) => (
          <div
            key={i}
            className="flex items-center gap-2.5 font-mono"
            style={{
              fontSize: 12,
              letterSpacing: "0.02em",
              color: i === step ? "var(--text)" : "var(--text-muted)",
            }}
          >
            <span className="text-accent-ink">{i === step ? "→" : "✓"}</span>
            <span>{s}</span>
          </div>
        ))}
      </div>
    </div>
  );
}
