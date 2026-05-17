// Center pane of /app. Renders system / user / agent messages and
// (optionally) a "thinking" indicator that animates step-by-step while
// the next agent reply is being fabricated. Auto-scrolls to the latest
// message on each update.

"use client";

import { useEffect, useRef, useState } from "react";
import { AgentMarkdown } from "@/components/product/agent-markdown";
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
  thinkLabel: string;
  error?: string | null;
};

export function ChatThread({
  messages,
  thinking,
  thinkLabel,
  error,
}: Props) {
  const ref = useRef<HTMLDivElement>(null);
  const totalBodyLen = messages.reduce(
    (n, m) => n + (m.kind === "agent" ? m.body.length : 0),
    0,
  );
  useEffect(() => {
    if (ref.current) ref.current.scrollTop = ref.current.scrollHeight;
  }, [messages.length, thinking, thinkLabel, totalBodyLen, error]);

  return (
    <div
      ref={ref}
      data-testid="chat-thread"
      className="flex-1 overflow-y-auto flex flex-col gap-4 sm:gap-[18px] px-4 sm:px-7 lg:px-9 py-4 sm:py-5 lg:py-6"
    >
      {messages.map((m, i) => {
        if (m.kind === "system") return <SystemMsg key={i} msg={m} />;
        if (m.kind === "user") return <UserMsg key={i} msg={m} />;
        return <AgentMsg key={i} msg={m} idx={i} />;
      })}
      {thinking && <ThinkingMsg label={thinkLabel} />}
      {error && <ErrorMsg body={error} />}
    </div>
  );
}

function ErrorMsg({ body }: { body: string }) {
  return (
    <div
      className="self-center w-full max-w-[680px] text-[12px] sm:text-[12.5px] font-mono px-3 py-2.5 sm:px-3.5 sm:py-2.5"
      style={{
        color: "var(--brick)",
        border: "1px solid var(--brick)",
        letterSpacing: "0.02em",
      }}
    >
      {body}
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
        className="bg-text text-surface text-[13.5px] sm:text-[14px] leading-[1.45] sm:leading-[1.5] px-3 sm:px-4 py-2.5 sm:py-3 max-w-[85%] sm:max-w-[80%] lg:max-w-[78%]"
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

      <div className="pl-7 sm:pl-8 flex flex-col gap-3 sm:gap-3.5">
        {msg.answer && (
          <div
            className="font-sans font-extrabold text-[20px] sm:text-[22px] lg:text-[24px] leading-[1.1]"
            style={{ letterSpacing: "-0.03em" }}
          >
            <HighlightWord>{msg.answer}</HighlightWord>
          </div>
        )}
        <div
          className="text-[14px] sm:text-[14.5px] leading-[1.55] sm:leading-[1.6] text-text max-w-full lg:max-w-[720px]"
        >
          <AgentMarkdown source={msg.body} />
        </div>

        {msg.reasoning.length > 0 && (
          <>
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
                    className="
                      flex flex-col gap-1 sm:grid sm:items-baseline sm:gap-3.5
                      sm:[grid-template-columns:32px_72px_1fr]
                      lg:[grid-template-columns:36px_76px_1fr]
                      px-3 sm:px-3.5 py-2.5 sm:py-3
                    "
                    style={{
                      borderBottom:
                        i < msg.reasoning.length - 1
                          ? "1px solid var(--hair)"
                          : "none",
                    }}
                  >
                    <div className="flex items-baseline gap-2 sm:contents">
                      <Mono muted>{r.n}</Mono>
                      <Mono accent size={11} className="font-semibold">
                        {r.cite}
                      </Mono>
                    </div>
                    <span className="text-[12.5px] sm:text-[13px] leading-[1.5]">
                      {r.body}
                    </span>
                  </div>
                ))}
              </div>
            )}
          </>
        )}

        {msg.sources.length > 0 && (
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
        )}
      </div>
    </div>
  );
}

function ThinkingMsg({ label }: { label: string }) {
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
          <span>{label}</span>
        </div>
      </div>
    </div>
  );
}
