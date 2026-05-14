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

import { Suspense, useEffect, useMemo, useRef, useState } from "react";
import { useSearchParams } from "next/navigation";
import { AppHeader } from "@/components/product/app-header";
import { Sidebar } from "@/components/product/sidebar";
import { ChatThread } from "@/components/product/chat-thread";
import { Composer } from "@/components/product/composer";
import { CaseHeaderStrip } from "@/components/product/case-header-strip";
import { CaseUpgradePrompt } from "@/components/product/case-upgrade-prompt";
import { ParcelPane } from "@/components/product/parcel-pane";
import { AddressPill } from "@/components/product/address-pill";
import { ParcelFab } from "@/components/product/parcel-fab";
import { Drawer } from "@/components/drawer";
import { Sheet } from "@/components/sheet";
import { useKeyboardInset } from "@/lib/use-keyboard-inset";
import { useMediaQuery, BREAKPOINTS } from "@/lib/use-media-query";
import type { AgentMessage, Message } from "@/lib/mock";
import {
  extractParcelContext,
  type BackendMessage,
  type ParcelContext,
} from "@/lib/parcel";
import { humanizeToolUse } from "@/lib/reasoning";
import type { AgentReasoningStep } from "@/lib/mock";

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

// Top-level page wraps the inner component in Suspense because
// ``useSearchParams`` opts the tree into client-side rendering for
// the params hook. Without the boundary, ``next build`` refuses to
// prerender the route.
export default function ProductAppPage() {
  return (
    <Suspense fallback={<div className="h-dvh bg-surface" />}>
      <ProductAppPageInner />
    </Suspense>
  );
}


function ProductAppPageInner() {
  const [messages, setMessages] = useState<Message[]>([OPENING]);
  const [thinking, setThinking] = useState(false);
  // Honest indicator: starts as "Reading bylaw…" and updates to the
  // current tool name as `content_block_start` events arrive. No
  // pre-baked rotation.
  const [thinkLabel, setThinkLabel] = useState("Reading bylaw…");
  const [error, setError] = useState<string | null>(null);
  // Active session id is mirrored in state (so the sidebar can highlight
  // the active row) and in a ref (so streaming closures see the latest
  // value without re-binding). `setSessionId` updates both.
  const [activeSessionId, setActiveSessionId] = useState<string | null>(null);
  const sessionIdRef = useRef<string | null>(null);
  const setSessionId = (id: string | null) => {
    sessionIdRef.current = id;
    setActiveSessionId(id);
  };
  // Bumped after every successful chat turn / session switch — sidebar
  // refetches its list whenever this changes.
  const [sidebarRefresh, setSidebarRefresh] = useState(0);
  // Parcel context for the right pane. Derived from the current
  // session's spatial-join tool results; null when the conversation
  // has no address-bearing question yet.
  const [parcel, setParcel] = useState<ParcelContext | null>(null);
  const abortRef = useRef<AbortController | null>(null);
  // Mobile/tablet overlay state. Both default closed; opening one
  // doesn't close the other (parcel sheet on mobile sits above the
  // chat which sits behind the sidebar drawer when both happen, but
  // in practice only one is open at a time because each click is the
  // user's explicit choice).
  const [sidebarOpen, setSidebarOpen] = useState(false);
  const [parcelOpen, setParcelOpen] = useState(false);

  // Case-billing context. ``caseId`` is taken from the URL on mount
  // (the /cases/new flow redirects with ``?case_id=N``) and from the
  // backend's ``session`` SSE event on each turn. ``tier`` mirrors the
  // active credit's tier so the header can show the badge. ``upgradeOffer``
  // captures any in-flight ``case_upgrade_offer`` event the agent fired
  // via the ``request_tier_upgrade`` tool. ``budgetWarning`` captures
  // the ``case_budget_warning`` payload (Layer 1 nearing exhaustion).
  const searchParams = useSearchParams();
  const caseIdFromUrl = useMemo(() => {
    const raw = searchParams.get("case_id");
    if (!raw) return null;
    const n = Number(raw);
    return Number.isInteger(n) && n > 0 ? n : null;
  }, [searchParams]);
  const [caseId, setCaseId] = useState<number | null>(caseIdFromUrl);
  const caseIdRef = useRef<number | null>(caseIdFromUrl);
  const setCaseIdBoth = (id: number | null) => {
    caseIdRef.current = id;
    setCaseId(id);
  };
  const [caseTier, setCaseTier] = useState<string | null>(null);
  const [upgradeOffer, setUpgradeOffer] = useState<{
    case_id: number;
    current_tier: string;
    recommended_tier: string;
    reason: string;
  } | null>(null);
  const [budgetWarning, setBudgetWarning] = useState<{
    case_id: number;
    tier: string;
    remaining_tokens: number;
    tier_budget: number;
  } | null>(null);
  // Keep the URL-derived caseId in sync when the user navigates with
  // a different ?case_id= without a full reload.
  useEffect(() => {
    if (caseIdFromUrl !== null && caseIdFromUrl !== caseIdRef.current) {
      setCaseIdBoth(caseIdFromUrl);
      // New case binding → discard prior session id; the next send()
      // will mint a new session under this case.
      setSessionId(null);
    }
  }, [caseIdFromUrl]);  // eslint-disable-line react-hooks/exhaustive-deps

  // Viewport gates. We render the Sheet/Drawer overlay components
  // conditionally rather than via CSS `display: none`, so their
  // useScrollLock/useEffect mount-side-effects never fire on the
  // wrong breakpoint. Both return false during SSR and on first
  // client render — neither overlay is open at first paint anyway,
  // so this can't cause a flash.
  const isDesktop = useMediaQuery(BREAKPOINTS.lg);
  const isTabletOrMobile = !isDesktop;
  const isMobile = !useMediaQuery(BREAKPOINTS.sm);
  const isTablet = isTabletOrMobile && !isMobile;

  // iOS soft-keyboard tracking. Writes --abs-keyboard-inset on <body>;
  // the Composer reads it and translates above the keyboard. Only runs
  // below 1024px — desktops never trigger this.
  useKeyboardInset(true);

  // Re-pull the active session and snap our local state to the
  // server's authoritative copy. Two outputs:
  //   1. parcel pane (extractParcelContext)
  //   2. message list (translateHistory) — picks up reasoning steps
  //      that weren't visible during streaming because tool_use blocks
  //      precede the final text in the saved conversation.
  // The id we requested is captured at call time; if the user has
  // since switched sessions we drop the result on the floor rather
  // than clobber.
  const refreshFromSession = async (sessionId: string | null) => {
    if (!sessionId) {
      setParcel(null);
      return;
    }
    try {
      const res = await fetch(
        `/api/chat/sessions/${encodeURIComponent(sessionId)}`,
        { cache: "no-store" },
      );
      if (sessionIdRef.current !== sessionId) return; // user moved on
      if (!res.ok) {
        // Surface non-2xx so the parcel pane being stale is *visible*.
        // Previously we returned silently here, which masked a real
        // backend bug (session-detail 404 from a user_id format
        // mismatch) for a long time — the pane simply stopped
        // updating with no signal to the user or anyone watching
        // browser devtools casually. We never want a silent skip
        // again. Log the response body for ops/debug grep.
        const detail = await res.text().catch(() => "");
        console.error(
          `[refreshFromSession] HTTP ${res.status} for session ${sessionId}: ${detail.slice(0, 500)}`,
        );
        // Don't clobber a more informative error from the stream
        // phase. Only surface our message if no error is showing.
        setError((prev) =>
          prev ?? `Couldn't refresh session state (HTTP ${res.status}). ` +
            (detail.slice(0, 200) || "Pane may be out of date."),
        );
        return;
      }
      const data = (await res.json()) as {
        messages: BackendMessage[];
        case_id?: number | null;
        tier?: string | null;
      };
      setParcel(extractParcelContext(data.messages));
      setMessages(translateHistory(data.messages));
      // Keep the case-billing context aligned with the authoritative
      // server state — covers the case where the resume fallback
      // attached a case mid-turn that the SSE stream didn't surface.
      if (typeof data.case_id === "number") {
        setCaseIdBoth(data.case_id);
      }
      if (typeof data.tier === "string") {
        setCaseTier(data.tier);
      }
    } catch (e) {
      // Network blip. Same "don't overwrite stream errors" rule.
      if (sessionIdRef.current !== sessionId) return;
      console.error(
        `[refreshFromSession] fetch threw for session ${sessionId}:`,
        e,
      );
      setError((prev) =>
        prev ?? `Couldn't refresh session state: ${(e as Error).message}`,
      );
    }
  };

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
          case_id: caseIdRef.current,
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
              const data = JSON.parse(ev.data) as {
                session_id?: string;
                case_id?: number | null;
                tier?: string | null;
              };
              if (data.session_id) setSessionId(data.session_id);
              if (typeof data.case_id === "number") {
                setCaseIdBoth(data.case_id);
              }
              if (typeof data.tier === "string") {
                setCaseTier(data.tier);
              }
            } catch {
              // ignore malformed session event
            }
          } else if (ev.event === "case_upgrade_offer") {
            try {
              const data = JSON.parse(ev.data) as {
                case_id?: number;
                current_tier?: string;
                recommended_tier?: string;
                reason?: string;
              };
              if (
                typeof data.case_id === "number" &&
                typeof data.current_tier === "string" &&
                typeof data.recommended_tier === "string"
              ) {
                setUpgradeOffer({
                  case_id: data.case_id,
                  current_tier: data.current_tier,
                  recommended_tier: data.recommended_tier,
                  reason: data.reason || "Additional research depth required.",
                });
              }
            } catch {
              // ignore malformed upgrade offer
            }
          } else if (ev.event === "case_budget_warning") {
            try {
              const data = JSON.parse(ev.data) as {
                case_id?: number;
                tier?: string;
                remaining_tokens?: number;
                tier_budget?: number;
              };
              if (
                typeof data.case_id === "number" &&
                typeof data.tier === "string" &&
                typeof data.remaining_tokens === "number" &&
                typeof data.tier_budget === "number"
              ) {
                setBudgetWarning({
                  case_id: data.case_id,
                  tier: data.tier,
                  remaining_tokens: data.remaining_tokens,
                  tier_budget: data.tier_budget,
                });
              }
            } catch {
              // ignore malformed budget warning
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
      // Refresh the sidebar so a newly-created session, or an
      // updated message_count on the existing one, lands in the list.
      setSidebarRefresh((n) => n + 1);
      // Snap to authoritative session state: refreshes parcel pane
      // and replays reasoning steps that streaming didn't surface.
      // Reads sessionIdRef directly (not a captured local) so we
      // always see the post-stream value the SSE handler set.
      void refreshFromSession(sessionIdRef.current);
    }
  };

  const selectSession = async (id: string) => {
    if (id === activeSessionId) return;
    abortRef.current?.abort();
    setError(null);
    setThinking(false);
    try {
      const res = await fetch(
        `/api/chat/sessions/${encodeURIComponent(id)}`,
        { cache: "no-store" },
      );
      if (!res.ok) {
        setError(`Couldn't load that reading (HTTP ${res.status}).`);
        return;
      }
      const data = (await res.json()) as {
        messages: BackendMessage[];
        case_id?: number | null;
        tier?: string | null;
      };
      setMessages(translateHistory(data.messages));
      setSessionId(id);
      // Rehydrate the case-billing context from the server. The /v1/chat
      // resume path can fall back to the session's stored case_id, but
      // the UI also needs it to drive the header strip and (when null
      // on a session with prior turns) the legacy-session composer gate.
      setCaseIdBoth(
        typeof data.case_id === "number" ? data.case_id : null,
      );
      setCaseTier(typeof data.tier === "string" ? data.tier : null);
      setParcel(extractParcelContext(data.messages));
    } catch (e) {
      setError(`Couldn't load that reading: ${(e as Error).message}`);
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
    setSessionId(null);
    setMessages([OPENING]);
    setThinking(false);
    setThinkLabel("Reading bylaw…");
    setError(null);
    setParcel(null);
    setSidebarOpen(false);
  };

  // Drawer-aware versions of the sidebar callbacks. Selecting a
  // session or starting a new one on mobile should auto-close the
  // drawer so the user lands back in the chat thread.
  const onSelectFromDrawer = (id: string) => {
    setSidebarOpen(false);
    void selectSession(id);
  };

  return (
    // dvh tracks the iOS dynamic viewport so the composer doesn't
    // disappear behind the URL bar collapse/expand. overflow-hidden
    // keeps the chat thread's scroll contained.
    <div className="h-dvh flex flex-col bg-surface text-text overflow-hidden">
      <AppHeader
        reading={READING}
        onMenuClick={() => setSidebarOpen(true)}
      />
      {/* AddressPill is mobile-only; renders nothing once lg or once
       * there's no parcel. */}
      <AddressPill parcel={parcel} onClick={() => setParcelOpen(true)} />
      <div className="flex-1 flex min-h-0 relative">
        {/* Desktop sidebar (lg+ only). Below lg the sidebar lives
         * inside the Drawer below. */}
        <div className="hidden lg:contents">
          <Sidebar
            onNew={onNew}
            onSelect={selectSession}
            activeSessionId={activeSessionId}
            refreshTrigger={sidebarRefresh}
          />
        </div>

        <main className="flex-1 flex flex-col min-w-0 bg-surface">
          <ChatThread
            messages={messages}
            thinking={thinking}
            thinkLabel={thinkLabel}
            error={error}
          />
          {(caseTier || caseId !== null) && (
            <CaseHeaderStrip
              caseId={caseId}
              tier={caseTier}
              budgetWarning={budgetWarning}
            />
          )}
          {upgradeOffer && (
            <CaseUpgradePrompt
              offer={upgradeOffer}
              onClose={() => setUpgradeOffer(null)}
              onAccepted={(newTier) => {
                setCaseTier(newTier);
                setUpgradeOffer(null);
                setBudgetWarning(null);
              }}
            />
          )}
          {activeSessionId !== null && caseId === null ? (
            // Legacy session — predates the case-credit model and has
            // no case attached, so /v1/chat will reject every turn with
            // case_id_required. Replace the composer with a one-way
            // exit so users can't waste a question on it.
            <div className="border-t border-hair px-4 py-3 bg-surface-alt text-[13px] text-text-muted">
              This conversation predates our new case-based billing and
              can&rsquo;t be continued.{" "}
              <a href="/cases/new" className="underline text-text">
                Start a new case
              </a>{" "}
              to ask another question.
            </div>
          ) : (
            <Composer onSend={send} disabled={thinking} />
          )}
        </main>

        {/* Desktop parcel pane (lg+ only). Below lg the pane shows
         * inside Sheet (mobile) or as a side overlay (tablet). */}
        <div className="hidden lg:contents">
          <ParcelPane parcel={parcel} />
        </div>

        <ParcelFab
          onClick={() => setParcelOpen((o) => !o)}
          active={parcelOpen}
        />
      </div>

      {/* Mobile + tablet sidebar drawer. Desktop renders the sidebar
       * inline above and never opens this drawer. */}
      {isTabletOrMobile && (
        <Drawer
          open={sidebarOpen}
          onClose={() => setSidebarOpen(false)}
          side="left"
          width={300}
          ariaLabel="Recent readings"
        >
          <Sidebar
            onNew={onNew}
            onSelect={onSelectFromDrawer}
            activeSessionId={activeSessionId}
            refreshTrigger={sidebarRefresh}
            inDrawer
          />
        </Drawer>
      )}

      {/*
       * Parcel surface — three variants depending on viewport:
       *   - Mobile: bottom sheet (per design spec — anchored to bottom,
       *     drag handle, 80% max height).
       *   - Tablet: right-side drawer (320px, slides in from right).
       *     The design spec also calls for an in-flow side pane that
       *     pushes the chat narrower; we use an overlay instead so the
       *     chat width doesn't jump and the existing single-flex
       *     layout stays simple. This is a deliberate v1 trade-off —
       *     revisit if usage shows people want the chat width to
       *     adapt.
       *   - Desktop: handled inline above (always-on right pane).
       */}
      {isMobile && (
        <Sheet
          open={parcelOpen}
          onClose={() => setParcelOpen(false)}
          maxHeightPct={80}
          ariaLabel="Parcel details"
        >
          <ParcelPane parcel={parcel} inSheet />
        </Sheet>
      )}
      {isTablet && (
        <Drawer
          open={parcelOpen}
          onClose={() => setParcelOpen(false)}
          side="right"
          width={320}
          ariaLabel="Parcel details"
        >
          <ParcelPane parcel={parcel} inSheet />
        </Drawer>
      )}
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

// Convert a saved Anthropic-shape conversation into the simpler UI
// shape (system / user / agent rows). We collapse the tool-use loop:
// intermediate assistant turns that contain only tool_use blocks and
// user turns that carry tool_result are dropped, leaving just the
// human-readable turns. The opening system message is prepended so
// resumed sessions still show the "connected" banner.
function translateHistory(messages: BackendMessage[]): Message[] {
  const out: Message[] = [OPENING];
  // One agent message per user question. The tool-use loop can emit
  // many intermediate assistant turns ("Let me check X" → tool →
  // "Now let me also check Y" → tool → final answer); rendering all
  // of them inflates the chat and makes the post-stream snap (which
  // splits a single streamed message into N) jarring. Instead we
  // accumulate everything between user messages and emit one agent
  // turn whose body is the *last* text-bearing assistant turn (the
  // final answer) and whose reasoning is every tool call that
  // happened along the way.
  let pendingReasoning: AgentReasoningStep[] = [];
  let pendingFinalText = "";

  const flush = () => {
    if (!pendingFinalText.trim() && pendingReasoning.length === 0) return;
    out.push(
      buildAgentFromText(pendingFinalText.trim(), pendingReasoning),
    );
    pendingReasoning = [];
    pendingFinalText = "";
  };

  for (const m of messages) {
    if (m.role === "user") {
      if (typeof m.content === "string" && m.content.trim()) {
        flush();
        out.push({ kind: "user", body: m.content });
      }
      // tool_result intermediate → skip
      continue;
    }
    // assistant
    if (typeof m.content === "string") {
      // Defensive: future provider might collapse to string.
      pendingFinalText = m.content;
      continue;
    }
    for (const b of m.content) {
      if (b.type === "tool_use") {
        pendingReasoning.push(
          humanizeToolUse(b.name, b.input ?? {}, pendingReasoning.length),
        );
      }
    }
    const text = m.content
      .filter((b): b is { type: "text"; text: string } => b.type === "text")
      .map((b) => b.text)
      .join("");
    if (text.trim()) {
      // Overwrite — only the last text turn becomes the rendered body.
      pendingFinalText = text;
    }
  }
  flush();
  return out;
}

function buildAgentFromText(
  text: string,
  reasoning: AgentReasoningStep[] = [],
): AgentMessage {
  return {
    kind: "agent",
    answer: "",
    body: text,
    reasoning,
    confidence: 0.9,
    sources: [],
  };
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
