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
import { usePathname, useRouter, useSearchParams } from "next/navigation";
import { AppHeader } from "@/components/product/app-header";
import { Sidebar } from "@/components/product/sidebar";
import { ChatThread } from "@/components/product/chat-thread";
import {
  CaseToolbar,
  type CaseView,
} from "@/components/product/case-toolbar";
import {
  AnswerView,
  type AnswerPhase,
} from "@/components/product/answer-view";
import {
  humanizeQuestionSlug,
  type QuestionPurchaseResponse,
  type WalletResponse,
} from "@/lib/cases";
import { cn } from "@/lib/cn";
import type { SavedFeedback } from "@/components/product/message-feedback";
import { Composer } from "@/components/product/composer";
import { BalanceStrip } from "@/components/product/balance-strip";
import { TopUpPrompt } from "@/components/product/top-up-prompt";
import { ParcelPane } from "@/components/product/parcel-pane";
import { CitationViewerProvider } from "@/components/product/citation-viewer";
import type { CitationRef } from "@/lib/citations";
import { AddressPill } from "@/components/product/address-pill";
import { ParcelFab } from "@/components/product/parcel-fab";
import { ChatDisclaimerBar } from "@/components/product/chat-disclaimer-bar";
import { Drawer } from "@/components/drawer";
import { Sheet } from "@/components/sheet";
import { useKeyboardInset } from "@/lib/use-keyboard-inset";
import { useMediaQuery, BREAKPOINTS } from "@/lib/use-media-query";
import type { AgentMessage, Message } from "@/lib/mock";
import {
  collectCitations,
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

// The mutable slice of the wallet that rides on the per-turn
// ``token_balance`` SSE event. Everything else on ``WalletResponse``
// (``floor_tokens``, ``payments_enabled``, ``tokens_per_turn``) is static for
// the session and only ever comes from the seed fetch.
type BalancePatch = {
  balance_tokens?: number;
  approx_turns_remaining?: number;
  low_balance?: boolean;
};

/** Fold a per-turn ``token_balance`` patch onto a wallet snapshot.
 *
 * ``chat_enabled`` is recomputed from the patched balance against the
 * snapshot's floor because the SSE payload carries neither the floor nor
 * ``payments_enabled`` — that's what flips the workspace into (and out of)
 * the out-of-turns state without a reload. */
function applyBalancePatch(
  wallet: WalletResponse,
  patch: BalancePatch,
): WalletResponse {
  const next = { ...wallet };
  if (typeof patch.balance_tokens === "number") {
    next.balance_tokens = patch.balance_tokens;
    next.chat_enabled = patch.balance_tokens > wallet.floor_tokens;
  }
  if (typeof patch.approx_turns_remaining === "number") {
    next.approx_turns_remaining = patch.approx_turns_remaining;
  }
  if (typeof patch.low_balance === "boolean") {
    next.low_balance = patch.low_balance;
  }
  return next;
}

type CaseRecord = {
  id: number;
  user_case_number: number;
  anchor_kind: string;
  anchor_label: string;
  // ABS-423: terminal outcome of the case-open spatial join. Optional
  // because a backend that predates it simply omits the fields.
  spatial_status?: string | null;
  spatial_reason?: string | null;
};

/**
 * Resolve one case's record — anchor + user-facing case number (ABS-424).
 *
 * Prefers GET /api/cases/{id}, which always resolves for a case the caller
 * owns. Falls back to scanning the capped case list so the shell still works
 * against a backend that predates the single-case route. Returns null when
 * neither source can identify the case; the caller leaves state untouched
 * rather than painting a guess.
 */
async function fetchCaseRecord(caseId: number): Promise<CaseRecord | null> {
  const one = await fetch(`/api/cases/${caseId}`, { cache: "no-store" });
  if (one.ok) {
    const body = (await one.json()) as { case?: CaseRecord | null };
    if (body.case) return body.case;
  }
  const list = await fetch("/api/cases", { cache: "no-store" });
  if (!list.ok) return null;
  const data = (await list.json()) as { cases: CaseRecord[] };
  return data.cases.find((c) => c.id === caseId) ?? null;
}

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
  // Every clause the agent retrieved in this session, uncapped (ABS-451).
  // Feeds the inline-citation index so "(Section 442)" written in a reply
  // or a table cell opens the same clause drawer as the rail card. Kept
  // separate from `parcel.cited` because the parcel context only exists
  // once a spatial join lands — a pure bylaw-text question has citations
  // but no parcel.
  const [citations, setCitations] = useState<CitationRef[]>([]);
  const [feedbackMap, setFeedbackMap] = useState<Record<number, SavedFeedback>>({});
  const abortRef = useRef<AbortController | null>(null);
  // Per-case message snapshot. Saved when the user navigates away from a case
  // mid-stream so that navigating back restores at least the user's question
  // (and any partial reply) even if the server hasn't persisted the response
  // yet. Entries are cleared once the server response is at least as complete.
  const caseMessageCacheRef = useRef<Map<number, Message[]>>(new Map());
  // Guard for the URL-based session-restore effect. Tracks which case_id has
  // most recently been restored to prevent double-fetches from React Strict Mode
  // double-invoke and normal re-renders. Declared here (before the effect that
  // reads it) so the binding is initialized before the effect callback runs.
  const restoredCaseIdRef = useRef<number | null>(null);
  // Case whose ``?first_message=`` auto-send has already fired (ABS-449).
  // The restore effect skips that case: send() is the owner of its
  // transcript until the turn settles. Declared here for the same reason as
  // the ref above — the effect that reads it is defined earlier in the body.
  const autoSendCaseIdRef = useRef<number | null>(null);
  // True while an abort we asked for is in flight (case switch / "+ New
  // reading"). Lets send() tell an expected cancellation apart from a turn
  // that died on its own, which must surface an error + retry (ABS-449).
  const intentionalAbortRef = useRef(false);
  // Mobile/tablet overlay state. Both default closed; opening one
  // doesn't close the other (parcel sheet on mobile sits above the
  // chat which sits behind the sidebar drawer when both happen, but
  // in practice only one is open at a time because each click is the
  // user's explicit choice).
  const [sidebarOpen, setSidebarOpen] = useState(false);
  const [parcelOpen, setParcelOpen] = useState(false);

  // Case-billing context. ``caseId`` is taken from the URL on mount
  // (the /cases/new flow redirects with ``?case_id=N``) and from the
  // backend's ``session`` SSE event on each turn. The beta pivot (ABS-386)
  // bills chat by an account-level token wallet shown as "~N turns" — the
  // tier/credit machinery (caseTier / upgradeOffer / budgetWarning) is
  // retired. ``wallet`` is seeded from /api/billing/wallet and updated live
  // off the per-turn ``token_balance`` SSE event.
  const searchParams = useSearchParams();
  const router = useRouter();
  const pathname = usePathname();
  const caseIdFromUrl = useMemo(() => {
    const raw = searchParams.get("case_id");
    if (!raw) return null;
    const n = Number(raw);
    return Number.isInteger(n) && n > 0 ? n : null;
  }, [searchParams]);
  const caseNumberFromUrl = useMemo(() => {
    const raw = searchParams.get("case_number");
    if (!raw) return null;
    const n = Number(raw);
    return Number.isInteger(n) && n > 0 ? n : null;
  }, [searchParams]);
  // ABS-344: a report-backed case opens the SAME /app workspace via
  // ``?report_id=N``. The center pane then swaps between the purchased
  // report and a conversation on that case; the sidebar/parcel panes stay
  // shared. ``view`` is which face the center pane shows; the report's
  // lifecycle phase + settled purchase (fed up from AnswerView) drive the
  // header label and the parcel/seed context.
  const reportIdFromUrl = useMemo(() => {
    const raw = searchParams.get("report_id");
    if (!raw) return null;
    const n = Number(raw);
    return Number.isInteger(n) && n > 0 ? n : null;
  }, [searchParams]);
  const [view, setView] = useState<CaseView>("report");
  const [reportPhase, setReportPhase] = useState<AnswerPhase | null>(null);
  const [reportPurchase, setReportPurchase] =
    useState<QuestionPurchaseResponse | null>(null);
  // A fresh report_id starts on the report face (spec: generating → report,
  // then the user may toggle to conversation).
  useEffect(() => {
    setView("report");
    setReportPhase(null);
    setReportPurchase(null);
  }, [reportIdFromUrl]);

  // ABS-363: AnswerView (below) settles a report — success or failure — by
  // transitioning its own phase to "ready" (the coarse name covers
  // captured/voided/failed alike, see answer-view.tsx's classify()). That
  // phase already bubbles up to `reportPhase` via onPhaseChange, but nothing
  // previously told the sidebar to refetch, so a report that finished while
  // the user watched it generate kept showing the GENERATING pill in the
  // case list until a full reload. Bumping sidebarRefresh here re-runs the
  // sidebar's fetch so the badge flips live.
  useEffect(() => {
    if (reportPhase === "ready") {
      setSidebarRefresh((n) => n + 1);
    }
  }, [reportPhase]);

  const [caseId, setCaseId] = useState<number | null>(caseIdFromUrl);
  const caseIdRef = useRef<number | null>(caseIdFromUrl);
  const [caseNumber, setCaseNumber] = useState<number | null>(caseNumberFromUrl);
  const setCaseIdBoth = (id: number | null) => {
    // ABS-453: binding to a different case invalidates the current case
    // number. Clearing it here means the badge hides rather than showing the
    // *previous* case's number until the new one resolves.
    if (id !== caseIdRef.current) setCaseNumber(null);
    caseIdRef.current = id;
    setCaseId(id);
  };
  const [caseAnchor, setCaseAnchor] = useState<{
    kind: string;
    label: string;
    // ABS-423: terminal outcome of the case-open spatial join, so the
    // parcel pane shows the failure instead of a permanent "pending".
    spatialStatus?: string | null;
    spatialReason?: string | null;
  } | null>(null);
  // Token wallet (ABS-386). Seeded from /api/billing/wallet on mount, then
  // decremented live off the per-turn ``token_balance`` SSE event. Null while
  // the seed is in flight.
  const [wallet, setWallet] = useState<WalletResponse | null>(null);
  // ABS-460: the seed fetch and the first turn's ``token_balance`` event race.
  // The composer is live as soon as the shell paints, so a turn can settle
  // while the seed is still in flight — and then the seed lands carrying a
  // balance from *before* the burn. Two ways that used to lose the decrement:
  // the SSE patch arrived with ``wallet`` still null and was dropped on the
  // floor, or the seed's ``setWallet`` overwrote the patched snapshot. Either
  // way the strip painted the pre-burn turn count and the out-of-turns prompt
  // never appeared until a reload. Keep the last patch (and a counter of how
  // many have landed) in refs so a seed that resolves late can re-apply it.
  const lastBalancePatchRef = useRef<BalancePatch | null>(null);
  const balancePatchSeqRef = useRef(0);
  // Set when a send is refused for lack of tokens — either the client
  // pre-flight (wallet at/below floor) or a backend 402 ``insufficient_tokens``
  // that raced the client state. Reset on each accepted send and on a fresh
  // mount (e.g. returning from a top-up checkout). Derived ``outOfTokens``
  // below OR's it with the wallet's own ``chat_enabled`` flag.
  const [refused, setRefused] = useState(false);
  // Text of the last turn that failed (network error, backend error, empty
  // or truncated stream, or an abort we didn't ask for). Non-null means the
  // error block below renders a Retry button, so a dropped question is never
  // an unrecoverable dead end (ABS-449).
  const [failedSend, setFailedSend] = useState<string | null>(null);
  const outOfTokens = refused || (wallet !== null && !wallet.chat_enabled);
  // Keep the URL-derived caseId in sync when the user navigates with
  // a different ?case_id= without a full reload.
  useEffect(() => {
    if (caseIdFromUrl !== null && caseIdFromUrl !== caseIdRef.current) {
      setCaseIdBoth(caseIdFromUrl);
      // Use URL-supplied case_number if present; SSE event will update
      // it on the first chat turn if not.
      setCaseNumber(caseNumberFromUrl);
      // New case binding → discard prior session id; the next send()
      // will mint a new session under this case.
      setSessionId(null);
      // Allow the restore effect to re-run for the new case. Without
      // this reset, navigating A → B → A would skip restoring A on
      // return because restoredCaseIdRef still holds A from the first
      // visit (React Strict Mode double-invoke guard now resets cleanly
      // between distinct navigations).
      restoredCaseIdRef.current = null;
    }
  }, [caseIdFromUrl, caseNumberFromUrl]);  // eslint-disable-line react-hooks/exhaustive-deps

  // Seed the token wallet on mount (and after returning from a top-up
  // checkout — a full navigation back to /app re-runs this). The balance is
  // then kept live by the per-turn ``token_balance`` SSE event; this fetch is
  // the initial paint + the source of truth for ``payments_enabled`` and the
  // pre-flight floor, neither of which rides on the SSE payload.
  const refreshWallet = async () => {
    const patchSeqAtStart = balancePatchSeqRef.current;
    try {
      const res = await fetch("/api/billing/wallet", { cache: "no-store" });
      if (!res.ok) return;
      const seeded = (await res.json()) as WalletResponse;
      // A turn settled while this fetch was in flight, so its ``token_balance``
      // is newer than the body we just received — re-apply it rather than
      // painting a stale pre-burn balance over it (ABS-460). When no patch
      // landed during the fetch the seed is authoritative and goes in as-is,
      // which is what makes a post-top-up refresh still raise the balance.
      const racedPatch =
        balancePatchSeqRef.current !== patchSeqAtStart
          ? lastBalancePatchRef.current
          : null;
      const w = racedPatch ? applyBalancePatch(seeded, racedPatch) : seeded;
      setWallet(w);
      // A fresh, chat-enabled wallet clears any prior refusal (e.g. the user
      // topped up and came back).
      if (w.chat_enabled) setRefused(false);
    } catch {
      // Non-critical: the strip simply doesn't paint until the next turn's
      // token_balance event arrives.
    }
  };
  useEffect(() => {
    void refreshWallet();
  }, []);  // eslint-disable-line react-hooks/exhaustive-deps

  // Fetch the case anchor (kind + label) whenever caseId changes so
  // the parcel pane can show the address even before a spatial lookup.
  // ABS-453: the same response carries ``user_case_number``, so this is also
  // the earliest reliable source for the badge's case number on a direct URL
  // load that omits ?case_number= (the SSE ``session`` event only arrives on
  // the first turn). Picking it up here means the badge paints the
  // user-facing number rather than flashing the internal id first.
  useEffect(() => {
    if (!caseId) {
      setCaseAnchor(null);
      return;
    }
    let cancelled = false;
    void (async () => {
      try {
        // ABS-424: ask for *this* case directly. GET /api/cases is capped at
        // the newest N, so a user deep in their case history could open an
        // older case and never find it in the list — leaving the footer
        // without a number until the first turn's SSE ``session`` event
        // supplied one, which read as the case number changing identity
        // mid-conversation. The single-case route always resolves. The list
        // stays as a fallback for a deployment whose backend predates it.
        const detail = await fetchCaseRecord(caseId);
        // A newer caseId won the race while this was in flight — its own
        // run of this effect owns the state now.
        if (cancelled || !detail) return;
        setCaseAnchor({
          kind: detail.anchor_kind,
          label: detail.anchor_label,
          // ABS-423: both sources project ``CaseOut``, so the terminal
          // spatial-join outcome rides along and the parcel pane can render
          // a real failure instead of an eternal "geocoding pending".
          spatialStatus: detail.spatial_status ?? null,
          spatialReason: detail.spatial_reason ?? null,
        });
        if (typeof detail.user_case_number === "number") {
          setCaseNumber(detail.user_case_number);
        }
      } catch {
        // Non-critical — parcel pane falls back to generic empty state.
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [caseId]);  // eslint-disable-line react-hooks/exhaustive-deps

  // On direct URL load (reload, share link, browser back/forward) with
  // a ?case_id=N param but no ?first_message=, restore the most recent
  // session for that case so the transcript and parcel pane aren't
  // empty. Skipped when first_message is present — that flow mints a
  // new session via send() and handles its own state hydration.
  useEffect(() => {
    if (caseIdFromUrl === null) return;
    if (searchParams.get("first_message")) return;
    // ABS-449: never restore over a turn that is already in flight. The
    // auto-send below strips ``first_message`` with router.replace BEFORE
    // awaiting send(), so this effect re-runs one render later with the
    // param gone — and by then POST /v1/chat has already created the (still
    // empty) chat session row server-side. Without this guard the fetch
    // below finds that row, calls selectSession(), and selectSession's
    // ``abortRef.current?.abort()`` kills the very stream that created it.
    // The user's opening question was then never persisted and no error
    // ever surfaced: the case sat with an unanswered question forever.
    if (abortRef.current !== null) return;
    // Same race, slower variant: if the auto-send for THIS case already
    // fired, send() owns hydration for it (it calls refreshFromSession when
    // the turn settles). Restoring here would either abort the stream or
    // clobber the optimistic transcript with an empty server copy.
    if (autoSendCaseIdRef.current === caseIdFromUrl) return;
    // Guard against running twice for the same case (React Strict Mode
    // double-invoke, or a re-render triggered by state settling).
    if (restoredCaseIdRef.current === caseIdFromUrl) return;
    restoredCaseIdRef.current = caseIdFromUrl;

    void (async () => {
      try {
        const res = await fetch("/api/chat/sessions", { cache: "no-store" });
        if (!res.ok) return;
        const data = (await res.json()) as {
          sessions: Array<{ session_id: string; case_id?: number | null }>;
        };
        // Sessions are already newest-first from the backend; pick the
        // first one whose case_id matches the URL param.
        const match = data.sessions.find((s) => s.case_id === caseIdFromUrl);
        if (match) {
          await selectSession(match.session_id);
        }
      } catch {
        // Non-critical: the page still renders; the user can click the
        // sidebar to manually restore the session.
      }
    })();
  }, [caseIdFromUrl, searchParams]);  // eslint-disable-line react-hooks/exhaustive-deps

  // Auto-send the first message that the case-open form passed via
  // ``?first_message=...``. Runs once: a ref guard handles React Strict
  // Mode's double-mount, and we strip the param via ``router.replace``
  // before awaiting send() so a refresh mid-stream doesn't replay it.
  // We require ``caseId`` to be set first — sending without one would
  // hit the no-active-case banner and 400 the chat call.
  const autoSentFirstMessageRef = useRef(false);
  useEffect(() => {
    if (autoSentFirstMessageRef.current) return;
    if (caseIdFromUrl === null) return;
    const firstMessage = searchParams.get("first_message");
    if (!firstMessage) return;
    autoSentFirstMessageRef.current = true;
    autoSendCaseIdRef.current = caseIdFromUrl;
    const cleaned = new URLSearchParams(searchParams.toString());
    cleaned.delete("first_message");
    const nextUrl =
      cleaned.size > 0 ? `${pathname}?${cleaned.toString()}` : pathname;
    router.replace(nextUrl, { scroll: false });
    void send(firstMessage);
  }, [caseIdFromUrl, searchParams, pathname, router]);  // eslint-disable-line react-hooks/exhaustive-deps

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
      setCitations([]);
      return;
    }
    try {
      const [res, fbMap] = await Promise.all([
        fetch(
          `/api/chat/sessions/${encodeURIComponent(sessionId)}`,
          { cache: "no-store" },
        ),
        fetchFeedback(sessionId),
      ]);
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
        message_db_ids?: number[];
        case_id?: number | null;
        case_number?: number | null;
      };
      const enriched = attachDbIds(data.messages, data.message_db_ids);
      setParcel(extractParcelContext(enriched));
      setCitations(collectCitations(enriched));
      setMessages(translateHistory(enriched));
      setFeedbackMap(fbMap);
      // Keep the case-billing context aligned with the authoritative
      // server state — covers the case where the resume fallback
      // attached a case mid-turn that the SSE stream didn't surface.
      if (typeof data.case_id === "number") {
        setCaseIdBoth(data.case_id);
      }
      if (typeof data.case_number === "number") {
        setCaseNumber(data.case_number);
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
    // Client pre-flight (ABS-386): if the wallet is at/below the floor, refuse
    // locally without POSTing. The composer is already disabled in this state,
    // so this mainly guards the programmatic path (auto-send of a
    // ``?first_message=`` after a balance drop). Nothing is appended or
    // cleared — the typed text is not lost.
    if (wallet !== null && !wallet.chat_enabled) {
      setRefused(true);
      return;
    }
    setRefused(false);
    setMessages((prev) => [...prev, { kind: "user", body: text }]);
    setThinking(true);
    setThinkLabel("Reading bylaw…");
    setError(null);
    setFailedSend(null);

    const stopThinking = () => {
      setThinking(false);
    };

    // Every way this turn can end without an answer routes through here, so
    // the user always gets both a reason and a way to try again (ABS-449).
    const failTurn = (message: string) => {
      setError(message);
      setFailedSend(text);
    };

    const ctrl = new AbortController();
    abortRef.current = ctrl;
    intentionalAbortRef.current = false;

    // Set when the turn is refused out-of-tokens (402). Guards the post-turn
    // refreshFromSession in the finally: reloading server history for a
    // resumed session would otherwise drop the optimistic user bubble and
    // lose the typed message.
    let refusedThisTurn = false;
    // Set when the turn ends via abort. Also guards the post-turn
    // refreshFromSession: an aborted turn wrote nothing server-side, so
    // reloading history would erase the optimistic user bubble we're asking
    // the user to retry.
    let abortedThisTurn = false;

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
        if (res.status === 402) {
          // Out-of-tokens pre-flight refusal (ABS-383 backend). Render the
          // TopUpPrompt state instead of a generic "Backend error (402)"
          // toast. The optimistic user bubble stays in the thread, so the
          // typed message is not lost, and no assistant stream started.
          let turns = 0;
          try {
            const parsed = JSON.parse(detail) as {
              detail?: { approx_turns_remaining?: number };
            };
            if (typeof parsed.detail?.approx_turns_remaining === "number") {
              turns = parsed.detail.approx_turns_remaining;
            }
          } catch {
            // Non-JSON 402 body — fall through with turns = 0.
          }
          refusedThisTurn = true;
          setRefused(true);
          setWallet((prev) =>
            prev
              ? {
                  ...prev,
                  approx_turns_remaining: turns,
                  low_balance: true,
                  chat_enabled: false,
                }
              : prev,
          );
          return;
        }
        failTurn(
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
                case_number?: number | null;
              };
              if (data.session_id) setSessionId(data.session_id);
              if (typeof data.case_id === "number") {
                setCaseIdBoth(data.case_id);
              }
              if (typeof data.case_number === "number") {
                setCaseNumber(data.case_number);
              }
            } catch {
              // ignore malformed session event
            }
          } else if (ev.event === "token_balance") {
            // Per-turn wallet decrement (ABS-383/386). Updates the
            // BalanceStrip live without a reload. ``payments_enabled`` and the
            // pre-flight floor are NOT on this payload — they come from the
            // wallet seed — so we recompute ``chat_enabled`` from the fresh
            // balance vs. the seed floor to flip into/out of the out-of-turns
            // state.
            try {
              const data = JSON.parse(ev.data) as BalancePatch;
              // Record the patch before applying it: if the seed is still in
              // flight there is no snapshot to fold it into yet, and this ref
              // is how ``refreshWallet`` learns it must re-apply the burn when
              // its (older) body finally lands (ABS-460).
              lastBalancePatchRef.current = data;
              balancePatchSeqRef.current += 1;
              setWallet((prev) =>
                prev ? applyBalancePatch(prev, data) : prev,
              );
            } catch {
              // ignore malformed token_balance event
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
        failTurn(humanizeBackendError(backendError));
      } else if (!agentStarted) {
        failTurn(
          "The agent didn't return any text. Try rephrasing — for an " +
            "address question, include the civic number and street " +
            "(e.g. \"What's the zone of 1967 Woodlawn Terrace?\").",
        );
      } else if (!messageStopped) {
        failTurn(
          "The agent's response was cut off before completion. Try " +
            "asking again, or simplify the question.",
        );
      }
    } catch (e) {
      if ((e as Error).name === "AbortError") {
        abortedThisTurn = true;
        // An abort we asked for (case switch, "+ New reading") is expected
        // — the user moved on, so stay quiet. Any other abort means the
        // request died with the question unanswered, which used to be
        // completely silent (ABS-449).
        if (!intentionalAbortRef.current) {
          stopThinking();
          failTurn(
            "Your question wasn't sent — the request was interrupted " +
              "before the agent could answer it. Nothing was charged.",
          );
        }
      } else {
        failTurn(`Network error: ${(e as Error).message}`);
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
      // always see the post-stream value the SSE handler set. Skipped on an
      // out-of-tokens refusal — nothing changed server-side, and reloading
      // history would drop the optimistic (still-typed) user message.
      // Also skipped on an abort: the turn wrote nothing, so the server copy
      // is behind the optimistic transcript and reloading it would drop the
      // question the user is being asked to retry (ABS-449).
      if (!refusedThisTurn && !abortedThisTurn) {
        void refreshFromSession(sessionIdRef.current);
      }
    }
  };

  // Abort the turn in flight because the user asked for something else
  // (switching cases, starting a new reading). Flagged so send() doesn't
  // report it as a failure.
  const abortActiveTurn = () => {
    if (!abortRef.current) return;
    intentionalAbortRef.current = true;
    abortRef.current.abort();
  };

  // Re-send the question from a failed turn. The failed attempt left an
  // optimistic user bubble (and possibly a partial reply) in the thread, so
  // trim from that bubble onward before re-sending — otherwise the retry
  // posts the same question twice in the transcript.
  const retryFailedSend = () => {
    const text = failedSend;
    if (text === null || thinking) return;
    setFailedSend(null);
    setError(null);
    setMessages((prev) => {
      for (let i = prev.length - 1; i >= 0; i--) {
        const m = prev[i];
        if (m.kind === "user" && m.body === text) return prev.slice(0, i);
      }
      return prev;
    });
    void send(text);
  };

  const selectSession = async (id: string, { updateUrl = false }: { updateUrl?: boolean } = {}) => {
    if (id === activeSessionId) return;
    // Compare against the ref too: the streaming SSE handler writes the new
    // session id to the ref immediately, while the state copy lands a render
    // later. Without this, a caller that races a live stream (the restore
    // effect, a double sidebar click) would abort the stream and reload the
    // session it is already on (ABS-449).
    if (id === sessionIdRef.current) return;

    // Snapshot current case messages before switching away. This preserves
    // the user's in-flight question (and any partial streaming reply) so
    // that navigating back to this case shows it even if the server hasn't
    // persisted the response yet. Only worth caching when there's more than
    // just the opening system message.
    if (caseIdRef.current !== null && messages.length > 1) {
      caseMessageCacheRef.current.set(caseIdRef.current, messages);
    }

    abortActiveTurn();
    setError(null);
    setFailedSend(null);
    setThinking(false);
    try {
      const [res, fbMap] = await Promise.all([
        fetch(
          `/api/chat/sessions/${encodeURIComponent(id)}`,
          { cache: "no-store" },
        ),
        fetchFeedback(id),
      ]);
      if (!res.ok) {
        setError(`Couldn't load that reading (HTTP ${res.status}).`);
        return;
      }
      const data = (await res.json()) as {
        messages: BackendMessage[];
        message_db_ids?: number[];
        case_id?: number | null;
        case_number?: number | null;
      };
      const enriched = attachDbIds(data.messages, data.message_db_ids);
      const newCaseId = typeof data.case_id === "number" ? data.case_id : null;

      // Prefer cached messages when they contain more turns than the server
      // returned — this happens when the stream was aborted mid-flight and
      // the backend hasn't yet saved the response. Once the server catches up
      // (or has always been ahead) we drop the cache entry and use the
      // authoritative server copy.
      const serverMessages = translateHistory(enriched);
      const cachedMessages = newCaseId !== null
        ? caseMessageCacheRef.current.get(newCaseId)
        : undefined;
      const messagesToShow =
        cachedMessages !== undefined && cachedMessages.length > serverMessages.length
          ? cachedMessages
          : serverMessages;
      if (newCaseId !== null && serverMessages.length >= (cachedMessages?.length ?? 0)) {
        caseMessageCacheRef.current.delete(newCaseId);
      }

      setMessages(messagesToShow);
      setFeedbackMap(fbMap);
      setSessionId(id);
      setCaseIdBoth(newCaseId);
      // ABS-453: only overwrite when the restore actually carried a number —
      // blanking it would drop a number the case list already resolved and
      // make the badge disappear after a restore.
      if (typeof data.case_number === "number") {
        setCaseNumber(data.case_number);
      }
      setParcel(extractParcelContext(enriched));
      setCitations(collectCitations(enriched));
      // Keep URL in sync so reloads and shared links land on the right case.
      // Only user-initiated calls (updateUrl=true) update the URL; the
      // restore effect passes updateUrl=false so it never races with a
      // concurrent user click and reverts the URL to the prior case.
      if (updateUrl && newCaseId !== null) {
        const params = new URLSearchParams(searchParams.toString());
        params.set("case_id", String(newCaseId));
        params.delete("case_number");
        params.delete("report_id");
        router.replace(`${pathname}?${params.toString()}`);
      }
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
    // "+ New reading" routes through the full case-open flow so the
    // user supplies an anchor + first message rather than silently
    // continuing the current case (the old local-reset body left
    // ``caseId`` from the URL bound, so a click looked like "fresh
    // chat" but actually kept billing on the prior case). Abort any
    // in-flight stream so it doesn't keep mutating state after we
    // navigate; the next /app mount starts fresh.
    abortActiveTurn();
    setSidebarOpen(false);
    router.push("/cases/new");
  };

  // Drawer-aware versions of the sidebar callbacks. Selecting a
  // session or starting a new one on mobile should auto-close the
  // drawer so the user lands back in the chat thread.
  const onSelectFromDrawer = (id: string) => {
    setSidebarOpen(false);
    void selectSession(id, { updateUrl: true });
  };

  // ── ABS-344 workspace derivations ──────────────────────────────────────
  const isReportBacked = reportIdFromUrl !== null;
  const reportContent = reportPurchase?.report ?? null;

  // Header label tracks which face the workspace is showing. CONVERSATION
  // whenever the conversation face is up (or a plain conversation-only case);
  // GENERATING only while the engine is *actually* running; REPORT otherwise.
  //
  // ABS-361: "REPORT" is the default for a report-backed workspace — including
  // the brief `loading`/`null` phase right after switching to a different
  // report, while its GET is in flight. Previously anything that wasn't yet
  // "ready" fell through to "GENERATING", so switching between two ALREADY
  // COMPLETED reports flashed a stale "GENERATING …" in the status bar (the
  // freshly-remounted <AnswerView> resets reportPhase to null before its
  // purchase GET resolves to "ready"). That read like the report was
  // regenerating/re-charging. Only a real `generating` phase — a report whose
  // background engine job is running — should say GENERATING now.
  const headerLabel =
    !isReportBacked || view === "conversation"
      ? "CONVERSATION"
      : reportPhase === "generating"
        ? "GENERATING"
        : "REPORT";

  // Prefer a resolved parcel for the header reading; then the report's own
  // subject; then the case anchor; finally the static fallback.
  const headerReading = reportContent
    ? {
        addr: reportContent.address,
        zone: reportContent.zone_subtitle || "—",
      }
    : caseAnchor
      ? { addr: caseAnchor.label, zone: "—" }
      : READING;

  // Seed a report-backed conversation with a system line noting the report +
  // parcel are in context, so follow-ups read as grounded in the purchased
  // answer (spec). Only once the report subject is known.
  const reportSeed: Message | null = useMemo(() => {
    if (!isReportBacked || !reportContent || !reportPurchase) return null;
    const title = humanizeQuestionSlug(reportPurchase.question_slug);
    const bits = [reportContent.address, reportContent.zone_subtitle].filter(
      (s): s is string => Boolean(s && s.trim()),
    );
    const subject = bits.length ? ` · ${bits.join(" · ")}` : "";
    return {
      kind: "system",
      body: `Report in context · ${title}${subject} · Follow-ups are grounded in your purchased answer.`,
    };
  }, [isReportBacked, reportContent, reportPurchase]);

  const conversationMessages = reportSeed
    ? [reportSeed, ...messages]
    : messages;

  // The shared parcel pane anchors on the report subject when report-backed,
  // else the case anchor.
  const paneAnchorLabel = reportContent?.address ?? caseAnchor?.label;
  const paneAnchorKind = reportContent ? "address" : caseAnchor?.kind;
  // ABS-423: the spatial status describes the *case anchor*. A report-backed
  // pane may be showing the report's subject address instead, so the failure
  // note only applies when the pane is rendering the case anchor itself.
  const paneSpatialStatus = reportContent ? null : caseAnchor?.spatialStatus;
  const paneSpatialReason = reportContent ? null : caseAnchor?.spatialReason;

  // ABS-346: the report's "Export PDF" renders the ReportDocument (letterhead
  // → findings → verification footer) via the dedicated report print surface —
  // NOT window.print() on the whole workspace (which would capture the chat
  // chrome/sidebar) and NOT the session transcript. Opens in a new tab so the
  // export is a clean, self-contained page that auto-triggers the print dialog.
  const handleExportReport = () => {
    if (reportIdFromUrl === null || typeof window === "undefined") return;
    window.open(`/app/print?report_id=${reportIdFromUrl}`, "_blank");
  };
  const handleShareReport = async () => {
    try {
      if (
        typeof navigator !== "undefined" &&
        navigator.clipboard &&
        typeof window !== "undefined"
      ) {
        await navigator.clipboard.writeText(window.location.href);
      }
    } catch {
      // Clipboard blocked (permissions / insecure context) — no-op; the
      // Share affordance is best-effort chrome, not a critical path.
    }
  };

  return (
    // The citation viewer wraps the whole workspace so the clause drawer
    // is a single shared surface: rail cards, inline references in agent
    // prose, and table cells all open the same panel (ABS-451).
    <CitationViewerProvider citations={citations}>
    {/* dvh tracks the iOS dynamic viewport so the composer doesn't
      * disappear behind the URL bar collapse/expand. overflow-hidden
      * keeps the chat thread's scroll contained. */}
    <div className="h-dvh flex flex-col bg-surface text-text overflow-hidden">
      <AppHeader
        reading={headerReading}
        label={headerLabel}
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
            onSelect={(id) => selectSession(id, { updateUrl: true })}
            activeSessionId={activeSessionId}
            activeReportId={reportIdFromUrl}
            refreshTrigger={sidebarRefresh}
          />
        </div>

        <main className="flex-1 flex flex-col min-w-0 bg-surface">
          {/* ABS-344: the CaseToolbar wraps the center pane on a
           * report-backed case, swapping report ↔ conversation. Hidden for
           * a conversation-only case (no report to toggle to). */}
          {isReportBacked && (
            <CaseToolbar
              view={view}
              onToggle={setView}
              showToggle
              // Share / Export act on the report document, so they only wire up
              // once the report has actually rendered (not while GENERATING).
              onShare={reportContent ? handleShareReport : undefined}
              onExport={reportContent ? handleExportReport : undefined}
            />
          )}

          {/* Report face — kept mounted (CSS-hidden when the conversation
           * face is up) so toggling back doesn't re-run the engine. Owns the
           * GENERATING → report lifecycle and feeds phase/subject upward. */}
          {reportIdFromUrl !== null && (
            <div
              className={cn(
                "flex-1 overflow-y-auto",
                view === "report" ? "" : "hidden",
              )}
              data-testid="report-canvas"
            >
              <div className="px-5 sm:px-8 py-8 lg:py-10 mx-auto max-w-[820px]">
                <AnswerView
                  key={reportIdFromUrl}
                  purchaseId={reportIdFromUrl}
                  onPhaseChange={setReportPhase}
                  onPurchaseChange={setReportPurchase}
                />
              </div>
            </div>
          )}

          {/* Conversation face — the existing chat thread. Shown for a
           * conversation-only case, or when a report-backed case is toggled
           * to Conversation. */}
          {(!isReportBacked || view === "conversation") && (
            <>
              <ChatThread
                messages={conversationMessages}
                thinking={thinking}
                thinkLabel={thinkLabel}
                error={error}
                onRetry={failedSend !== null ? retryFailedSend : undefined}
                sessionId={activeSessionId}
                feedbackMap={feedbackMap}
              />
              {caseId !== null && wallet !== null && (
                <BalanceStrip
                  caseId={caseId}
                  caseNumber={caseNumber}
                  approxTurnsRemaining={wallet.approx_turns_remaining}
                  lowBalance={wallet.low_balance}
                  paymentsEnabled={wallet.payments_enabled}
                />
              )}
              {caseId !== null && outOfTokens && (
                <TopUpPrompt paymentsEnabled={wallet?.payments_enabled ?? false} />
              )}
              <ChatDisclaimerBar />
              {caseId === null ? (
            // No active case → /v1/chat would 400 case_id_required.
            // Two sub-states share this gate:
            //   - activeSessionId !== null: a legacy session loaded
            //     from the sidebar that predates the case-credit model.
            //   - activeSessionId === null: user landed on /app with
            //     no ?case_id= in the URL (direct nav / bookmark /
            //     post-signin redirect). Either way, the entry point
            //     for billable chat is /cases/new.
            <div className="border-t border-hair px-4 py-3 bg-surface-alt text-[13px] text-text-muted">
              {activeSessionId !== null ? (
                <>
                  This conversation predates our current billing and
                  can&rsquo;t be continued.{" "}
                  <a href="/cases/new" className="underline text-text">
                    Start a new conversation
                  </a>{" "}
                  to ask another question.
                </>
              ) : (
                <>
                  To start a new conversation,{" "}
                  <a href="/cases/new" className="underline text-text">
                    open a case
                  </a>{" "}
                  first — it&rsquo;s free — pick the address, project, or DA
                  you&rsquo;re asking about.
                </>
              )}
            </div>
              ) : (
                <Composer
                  onSend={send}
                  disabled={thinking || outOfTokens}
                  outOfTokens={outOfTokens}
                  parcel={parcel}
                />
              )}
            </>
          )}
        </main>

        {/* Desktop parcel pane (lg+ only). Below lg the pane shows
         * inside Sheet (mobile) or as a side overlay (tablet). */}
        <div className="hidden lg:contents">
          <ParcelPane
            parcel={parcel}
            sessionId={activeSessionId}
            caseId={caseId}
            anchorLabel={paneAnchorLabel}
            anchorKind={paneAnchorKind}
            spatialStatus={paneSpatialStatus}
            spatialReason={paneSpatialReason}
            appendix={isReportBacked}
          />
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
            activeReportId={reportIdFromUrl}
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
          <ParcelPane
            parcel={parcel}
            sessionId={activeSessionId}
            caseId={caseId}
            anchorLabel={paneAnchorLabel}
            anchorKind={paneAnchorKind}
            spatialStatus={paneSpatialStatus}
            spatialReason={paneSpatialReason}
            appendix={isReportBacked}
            inSheet
          />
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
          <ParcelPane
            parcel={parcel}
            sessionId={activeSessionId}
            caseId={caseId}
            anchorLabel={paneAnchorLabel}
            anchorKind={paneAnchorKind}
            spatialStatus={paneSpatialStatus}
            spatialReason={paneSpatialReason}
            appendix={isReportBacked}
            inSheet
          />
        </Drawer>
      )}
    </div>
    </CitationViewerProvider>
  );
}

async function fetchFeedback(
  sessionId: string,
): Promise<Record<number, SavedFeedback>> {
  try {
    const res = await fetch(
      `/api/chat/sessions/${encodeURIComponent(sessionId)}/feedback`,
      { cache: "no-store" },
    );
    if (!res.ok) return {};
    const data = (await res.json()) as {
      feedback: Array<{
        message_id: number;
        rating: string | null;
        flag_reason: string | null;
        flag_notes: string | null;
      }>;
    };
    const map: Record<number, SavedFeedback> = {};
    for (const item of data.feedback) {
      map[item.message_id] = {
        rating: (item.rating as SavedFeedback["rating"]) ?? null,
        flag_reason: (item.flag_reason as SavedFeedback["flag_reason"]) ?? null,
        flag_notes: item.flag_notes,
      };
    }
    return map;
  } catch {
    return {};
  }
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
function attachDbIds(
  messages: BackendMessage[],
  dbIds?: number[],
): BackendMessage[] {
  if (!dbIds || dbIds.length === 0) return messages;
  return messages.map((m, i) => ({
    ...m,
    db_id: dbIds[i] ?? undefined,
  }));
}

function translateHistory(messages: BackendMessage[]): Message[] {
  const out: Message[] = [OPENING];
  let pendingReasoning: AgentReasoningStep[] = [];
  let pendingFinalText = "";
  let pendingDbId: number | undefined;

  const flush = () => {
    if (!pendingFinalText.trim() && pendingReasoning.length === 0) return;
    out.push(
      buildAgentFromText(pendingFinalText.trim(), pendingReasoning, pendingDbId),
    );
    pendingReasoning = [];
    pendingFinalText = "";
    pendingDbId = undefined;
  };

  for (const m of messages) {
    if (m.role === "user") {
      if (typeof m.content === "string" && m.content.trim()) {
        flush();
        out.push({ kind: "user", body: m.content });
      }
      continue;
    }
    // assistant
    if (typeof m.content === "string") {
      pendingFinalText = m.content;
      pendingDbId = m.db_id;
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
      pendingFinalText = text;
      pendingDbId = m.db_id;
    }
  }
  flush();
  return out;
}

function buildAgentFromText(
  text: string,
  reasoning: AgentReasoningStep[] = [],
  messageDbId?: number,
): AgentMessage {
  return {
    kind: "agent",
    answer: "",
    body: text,
    reasoning,
    confidence: 0.9,
    sources: [],
    messageDbId,
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
