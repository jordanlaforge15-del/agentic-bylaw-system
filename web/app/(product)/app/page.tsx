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

// We swap the indicator label based on which tool the agent is
// actually running. Anything we don't recognise falls back to
// "Reading bylaw…" — so the indicator never lies, only generalises.
const TOOL_LABELS: Record<string, string> = {
  list_documents: "Listing bylaw documents…",
  get_document_outline: "Reading the bylaw outline…",
  search_bylaw_evidence: "Searching bylaw evidence…",
  lookup_citation: "Looking up a citation…",
};

const READING = { addr: "Halifax Regional Centre", zone: "RC-LUB" };

const OPENING: Message = {
  kind: "system",
  body:
    "Connected · Regional Centre LUB indexed · Halifax zoning boundaries + " +
    "height/FAR/heritage/bonus schedules loaded · Google geocoder online. " +
    "Ask about a specific HRM address or about the bylaw text directly.",
};

export default function ProductAppPage() {
  const [messages, setMessages] = useState<Message[]>([OPENING]);
  const [thinking, setThinking] = useState(false);
  // Honest indicator: starts as "Reading bylaw…" and updates to the
  // current tool name as `content_block_start` events arrive. No
  // pre-baked rotation.
  const [thinkLabel, setThinkLabel] = useState("Reading bylaw…");
  const [error, setError] = useState<string | null>(null);
  const sessionIdRef = useRef<string | null>(null);
  const abortRef = useRef<AbortController | null>(null);

  const send = async (text: string) => {
    setMessages((prev) => [...prev, { kind: "user", body: text }]);
    setThinking(true);
    setThinkLabel("Reading bylaw…");
    setError(null);

    const stopThinking = () => {
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
      let backendError: string | null = null;
      let messageStopped = false;

      while (true) {
        const { value, done } = await reader.read();
        if (done) break;
        // Normalise CRLF → LF up-front. sse_starlette frames events
        // with \r\n\r\n by default; the parser below looks for \n\n
        // and splits lines on \n. Without this normalisation no
        // frame boundary is ever found and zero events are parsed.
        buffer += decoder.decode(value, { stream: true }).replace(/\r\n/g, "\n");

        // SSE frames are separated by blank lines.
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
          } else if (ev.event === "content_block_start") {
            // Tool-use blocks tell us what the agent is *actually*
            // doing. Update the indicator label so it reflects
            // reality. Text blocks are handled via content_block_delta.
            try {
              const data = JSON.parse(ev.data) as {
                content_block?: { type?: string; name?: string };
              };
              const block = data.content_block;
              if (block?.type === "tool_use" && block.name) {
                setThinkLabel(
                  TOOL_LABELS[block.name] || `Running ${block.name}…`,
                );
              }
            } catch {
              // ignore
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
          } else if (ev.event === "message_stop") {
            messageStopped = true;
          } else if (ev.event === "chat_error") {
            // Backend caught its own exception and surfaced a
            // structured error before closing the stream.
            try {
              const data = JSON.parse(ev.data) as {
                kind?: string;
                message?: string;
              };
              backendError =
                data.message ||
                "The agent couldn't complete this question.";
            } catch {
              backendError = "The agent couldn't complete this question.";
            }
          }
        }
      }

      // The reader closed cleanly. Now decide whether the response
      // was actually a complete answer. Three failure modes:
      //   1. backend emitted chat_error      → show that message
      //   2. stream cut off mid-content      → flag it
      //   3. stream ended with no content    → flag it
      stopThinking();
      if (backendError) {
        setError(humanizeBackendError(backendError));
      } else if (!agentStarted) {
        setError(
          "The agent didn't return any text. Try rephrasing — for an " +
            "address question, include the civic number and street " +
            "(e.g. \"What's the zone of 1967 Woodlawn Terrace?\").",
        );
      } else if (!messageStopped) {
        setError(
          "The agent's response was cut off before completion. Try " +
            "asking again, or simplify the question.",
        );
      }
    } catch (e) {
      if ((e as Error).name !== "AbortError") {
        setError(`Network error: ${(e as Error).message}`);
      }
    } finally {
      stopThinking();
      abortRef.current = null;
    }
  };

  // Translate the raw backend error text into something the user
  // can act on. The backend already strips traceback / internal
  // details, but the messages can still be opaque.
  function humanizeBackendError(raw: string): string {
    if (raw.includes("max_iterations")) {
      return (
        "The agent gave up after 10 tool calls without finding an " +
        "answer. Try rephrasing — be specific about the address or " +
        "bylaw section. If you asked about an address, make sure it's " +
        "within HRM."
      );
    }
    return `Backend error: ${raw}`;
  }

  const onNew = () => {
    abortRef.current?.abort();
    sessionIdRef.current = null;
    setMessages([OPENING]);
    setThinking(false);
    setThinkLabel("Reading bylaw…");
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
            thinkLabel={thinkLabel}
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
