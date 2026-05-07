// /app — three-pane chat product shell. Wired to the FastAPI advisor
// backend via /api/chat (server-side proxy → http://127.0.0.1:8000).
// The mock send() that hand-rolled boilerplate replies is gone; this
// version streams real LLM-generated text grounded in the indexed HRM
// bylaw.
//
// SSE event handling lives in this component because the proxy is
// dumb (verbatim byte forward). Events we care about:
//   session              → store session_id for follow-up turns
//   content_block_delta  → append text_delta to the streaming agent
//                          message
//   message_stop         → finalize (no-op; the reader will end)
// Tool-use events (the LLM calling search_bylaw_evidence etc.) are
// ignored for v1 — they'd power a real "reasoning steps" panel later.

"use client";

import { useRef, useState } from "react";
import { AppHeader } from "@/components/product/app-header";
import { Sidebar } from "@/components/product/sidebar";
import { ChatThread } from "@/components/product/chat-thread";
import { Composer } from "@/components/product/composer";
import { ParcelPane } from "@/components/product/parcel-pane";
import type { AgentMessage, Message } from "@/lib/mock";

// Cosmetic only — the backend doesn't emit progress events for tool
// calls yet, so we cycle through these steps to keep the UI alive
// while the LLM is thinking. Once the first text_delta arrives we
// drop the indicator entirely.
const STEPS = [
  "Locating relevant bylaw section…",
  "Searching bylaw evidence…",
  "Reading citations…",
  "Cross-checking schedules…",
  "Compiling answer…",
];

const READING = { addr: "Halifax Regional Centre", zone: "RC-LUB" };

const OPENING: Message = {
  kind: "system",
  body: "Connected to advisor · Regional Centre Land Use By-Law indexed.",
};

export default function ProductAppPage() {
  const [messages, setMessages] = useState<Message[]>([OPENING]);
  const [thinking, setThinking] = useState(false);
  const [thinkStep, setThinkStep] = useState(0);
  const [error, setError] = useState<string | null>(null);
  const sessionIdRef = useRef<string | null>(null);
  const abortRef = useRef<AbortController | null>(null);

  const send = async (text: string) => {
    setMessages((prev) => [...prev, { kind: "user", body: text }]);
    setThinking(true);
    setThinkStep(0);
    setError(null);

    // Cycle the cosmetic step indicator until the first text_delta
    // lands. We keep the interval id in a closure so the catch /
    // finally branches can clear it.
    const stepTimer = setInterval(() => {
      setThinkStep((s) => (s + 1) % STEPS.length);
    }, 700);
    const stopThinking = () => {
      clearInterval(stepTimer);
      setThinking(false);
    };

    const ctrl = new AbortController();
    abortRef.current = ctrl;

    try {
      const res = await fetch("/api/chat", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          message: text,
          session_id: sessionIdRef.current,
        }),
        signal: ctrl.signal,
      });

      if (!res.ok || !res.body) {
        const detail = await res.text().catch(() => "");
        stopThinking();
        setError(
          `Backend error (${res.status}). ${detail.slice(0, 240) || "No body."}`,
        );
        return;
      }

      const reader = res.body.getReader();
      const decoder = new TextDecoder();
      let buffer = "";
      let agentStarted = false;

      while (true) {
        const { value, done } = await reader.read();
        if (done) break;
        buffer += decoder.decode(value, { stream: true });

        // SSE frames are separated by blank lines (\n\n).
        let nl: number;
        while ((nl = buffer.indexOf("\n\n")) !== -1) {
          const raw = buffer.slice(0, nl);
          buffer = buffer.slice(nl + 2);
          const ev = parseSseEvent(raw);
          if (!ev) continue;

          if (ev.event === "session") {
            try {
              const data = JSON.parse(ev.data) as { session_id?: string };
              if (data.session_id) sessionIdRef.current = data.session_id;
            } catch {
              // ignore malformed session event
            }
          } else if (ev.event === "content_block_delta") {
            let data: { text_delta?: string | null } | null = null;
            try {
              data = JSON.parse(ev.data);
            } catch {
              continue;
            }
            const delta = data?.text_delta;
            if (typeof delta === "string" && delta.length > 0) {
              if (!agentStarted) {
                agentStarted = true;
                stopThinking();
              }
              appendAgentDelta(setMessages, delta);
            }
          }
          // message_stop, message_delta, content_block_start/stop:
          // intentionally ignored for v1.
        }
      }
    } catch (e) {
      if ((e as Error).name !== "AbortError") {
        setError(`Network error: ${(e as Error).message}`);
      }
    } finally {
      clearInterval(stepTimer);
      setThinking(false);
      abortRef.current = null;
    }
  };

  const onNew = () => {
    abortRef.current?.abort();
    sessionIdRef.current = null;
    setMessages([OPENING]);
    setThinking(false);
    setThinkStep(0);
    setError(null);
  };

  return (
    <div className="h-screen flex flex-col bg-surface text-text overflow-hidden">
      <AppHeader reading={READING} />
      <div className="flex-1 flex min-h-0">
        <Sidebar onNew={onNew} />
        <main className="flex-1 flex flex-col min-w-0 bg-surface">
          <ChatThread
            messages={messages}
            thinking={thinking}
            thinkSteps={STEPS}
            thinkStep={thinkStep}
            error={error}
          />
          <Composer onSend={send} disabled={thinking} />
        </main>
        <ParcelPane />
      </div>
    </div>
  );
}

function appendAgentDelta(
  setMessages: React.Dispatch<React.SetStateAction<Message[]>>,
  delta: string,
) {
  setMessages((prev) => {
    const last = prev[prev.length - 1];
    if (last?.kind === "agent") {
      const updated: AgentMessage = { ...last, body: last.body + delta };
      return [...prev.slice(0, -1), updated];
    }
    // First delta — open a fresh agent message.
    const fresh: AgentMessage = {
      kind: "agent",
      answer: "",
      body: delta,
      reasoning: [],
      confidence: 0.9,
      sources: [],
    };
    return [...prev, fresh];
  });
}

type SseEvent = { event: string; data: string };

function parseSseEvent(raw: string): SseEvent | null {
  let event = "message";
  const dataLines: string[] = [];
  for (const line of raw.split("\n")) {
    if (!line || line.startsWith(":")) continue; // blank / comment
    if (line.startsWith("event:")) {
      event = line.slice(6).trim();
    } else if (line.startsWith("data:")) {
      // SSE allows a leading single space after the colon; strip it.
      dataLines.push(line.slice(5).replace(/^ /, ""));
    }
  }
  if (dataLines.length === 0) return null;
  return { event, data: dataLines.join("\n") };
}
