// ABS-321 — post-purchase answer view + bounded refinement window.
//
// Lifecycle this island drives:
//   1. GET the purchase. If it is "authorized" (card held / credit
//      reserved but the engine hasn't run yet), POST /answer once to
//      produce the grounded result. Idempotent upstream — a settled
//      purchase comes back unchanged.
//   2. "captured" → render the raw engine answer (markdown) with the
//      ABS-313 disclaimer, plus the refinement composer.
//   3. "voided"/"failed" → the question couldn't be grounded; no charge.
//      Surface the reason and route back to the question menu.
//
// Refinement (ABS-312/317): a free, in-window follow-up. The upstream
// 409 guardrails are routed inline — a materially different question
// ("new_question") and an exhausted window ("window_exhausted") both
// dead-end to "buy a new answer" rather than being served for free.

"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import Link from "next/link";
import { AgentMarkdown } from "@/components/product/agent-markdown";
import { ReportDocument } from "@/components/product/report-document";
import { ReportGenerating } from "@/components/product/report-generating";
import { PaidAnswerDisclaimer } from "@/components/product/paid-answer-disclaimer";
import { ReadingIndicator } from "@/components/product/reading-indicator";
import { Btn } from "@/components/btn";
import { Mono } from "@/components/mono";
import {
  QuestionPurchaseResponse,
  humanizeQuestionSlug,
} from "@/lib/cases";

type LoadState =
  | { phase: "loading" }
  // ABS-343: the engine is running as a real background job. Carries the
  // in-flight purchase (when known) so the generation view can title the
  // report; `null` in the brief gap right after firing POST /answer.
  | { phase: "generating"; purchase: QuestionPurchaseResponse | null }
  | { phase: "ready"; purchase: QuestionPurchaseResponse }
  | { phase: "unauthorized" }
  | { phase: "notfound" }
  | { phase: "error"; message: string };

// A purchase in one of these states is settled — nothing left to run.
const SETTLED = new Set(["captured", "voided", "failed"]);

// ABS-343: while the background generation job runs, the view polls the
// purchase until it settles. ~1.5s cadence, capped so a wedged job
// eventually surfaces an error instead of spinning forever (~2 min).
const POLL_MS = 1500;
const MAX_POLLS = 80;

// Map a freshly-fetched purchase onto a load state. `generating` (job
// running) AND `authorized` (job runnable but not yet fired, e.g. a
// StrictMode double-mount landing here after the POST already went out)
// both drive the six-step generation view + poll. Everything else —
// captured / voided / failed and the defensive still-`authorizing` case —
// routes to the `ready` render, which picks the report, the no-charge card,
// or the "not ready yet" notice.
function classify(purchase: QuestionPurchaseResponse): LoadState {
  if (purchase.status === "generating" || purchase.status === "authorized") {
    return { phase: "generating", purchase };
  }
  return { phase: "ready", purchase };
}

// A routed refinement guardrail (the upstream 409 bodies). `kind`
// drives the copy; both kinds terminate the free window.
type RefineNotice = {
  kind: "new_question" | "window_exhausted" | "refinement_unavailable";
  suggestedSlug?: string;
};

const FAILURE_COPY: Record<string, string> = {
  zero_evidence:
    "The engine couldn't find bylaw evidence to ground an answer to this question, so it wasn't charged.",
  cost_ceiling:
    "This question ran past its reasoning budget before it could be grounded, so it wasn't charged.",
  internal_error:
    "Something went wrong while producing this answer, so it wasn't charged.",
};

// ABS-344: the coarse lifecycle phase, surfaced to an embedding parent so
// the unified case workspace can track the header label (GENERATING while
// the engine runs, REPORT once it's ready) and pull the report subject
// (address / zone) for the shared parcel pane + conversation seed.
export type AnswerPhase = LoadState["phase"];

export function AnswerView({
  purchaseId,
  onPhaseChange,
  onPurchaseChange,
}: {
  purchaseId: number;
  onPhaseChange?: (phase: AnswerPhase) => void;
  onPurchaseChange?: (purchase: QuestionPurchaseResponse | null) => void;
}) {
  const [state, setState] = useState<LoadState>({ phase: "loading" });
  const [refineText, setRefineText] = useState("");
  const [refining, setRefining] = useState(false);
  const [notice, setNotice] = useState<RefineNotice | null>(null);
  // Guards React 18 StrictMode's double-mount from firing two /answer
  // POSTs (the run is idempotent upstream, but a double request races
  // two loading states).
  const ranAnswer = useRef(false);

  const runAnswer = useCallback(async () => {
    setState({ phase: "generating", purchase: null });
    const r = await fetch(
      `/api/billing/questions/purchases/${purchaseId}/answer`,
      { method: "POST" },
    );
    if (r.status === 401) return setState({ phase: "unauthorized" });
    if (r.status === 404) return setState({ phase: "notfound" });
    if (!r.ok) {
      return setState({
        phase: "error",
        message: `Couldn't run the answer (HTTP ${r.status}).`,
      });
    }
    // ABS-343: POST now kicks off a background job and returns immediately —
    // usually with status `generating` (the poll effect below then watches
    // it settle), but it may already be settled (a stubbed/idempotent
    // response), so classify either way.
    const purchase = (await r.json()) as QuestionPurchaseResponse;
    setState(classify(purchase));
  }, [purchaseId]);

  // ABS-344: mirror the lifecycle up to an embedding parent. `phase` drives
  // the workspace header label; `purchase` (when settled) carries the report
  // subject the shared panes render.
  useEffect(() => {
    onPhaseChange?.(state.phase);
    onPurchaseChange?.(state.phase === "ready" ? state.purchase : null);
  }, [state, onPhaseChange, onPurchaseChange]);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      const r = await fetch(
        `/api/billing/questions/purchases/${purchaseId}`,
      );
      if (cancelled) return;
      if (r.status === 401) return setState({ phase: "unauthorized" });
      if (r.status === 404) return setState({ phase: "notfound" });
      if (!r.ok) {
        return setState({
          phase: "error",
          message: `Couldn't load your answer (HTTP ${r.status}).`,
        });
      }
      const purchase = (await r.json()) as QuestionPurchaseResponse;
      // Not yet run → kick off the engine once (fires the background job).
      if (purchase.status === "authorized" && !ranAnswer.current) {
        ranAnswer.current = true;
        await runAnswer();
        return;
      }
      // `generating` (a job already running — e.g. the user left and came
      // back) lands on the generation view and the poll effect below takes
      // over; a settled purchase renders straight away.
      setState(classify(purchase));
    })();
    return () => {
      cancelled = true;
    };
  }, [purchaseId, runAnswer]);

  // ABS-343: while the background job runs, poll the purchase until it
  // settles, then swap the generation view for the finished report. The
  // reveal is driven by the real job state (a `captured`/`voided`/`failed`
  // poll response), not by the generation view's own step timer.
  const generating = state.phase === "generating";
  useEffect(() => {
    if (!generating) return;
    let cancelled = false;
    let attempts = 0;
    let timer: ReturnType<typeof setTimeout> | undefined;

    const poll = async () => {
      if (cancelled) return;
      attempts += 1;
      let r: Response;
      try {
        r = await fetch(`/api/billing/questions/purchases/${purchaseId}`);
      } catch {
        // Transient network blip — keep trying until the ceiling.
        if (attempts >= MAX_POLLS) {
          return setState({
            phase: "error",
            message: "Lost contact while generating your report.",
          });
        }
        timer = setTimeout(poll, POLL_MS);
        return;
      }
      if (cancelled) return;
      if (r.status === 401) return setState({ phase: "unauthorized" });
      if (r.status === 404) return setState({ phase: "notfound" });
      if (!r.ok) {
        if (attempts >= MAX_POLLS) {
          return setState({
            phase: "error",
            message: `Couldn't load your report (HTTP ${r.status}).`,
          });
        }
        timer = setTimeout(poll, POLL_MS);
        return;
      }
      const purchase = (await r.json()) as QuestionPurchaseResponse;
      if (cancelled) return;
      if (SETTLED.has(purchase.status)) {
        return setState({ phase: "ready", purchase });
      }
      if (attempts >= MAX_POLLS) {
        return setState({
          phase: "error",
          message:
            "Your report is taking longer than expected. Refresh the page " +
            "to check again — it saves to your case, so nothing is lost.",
        });
      }
      // Keep the in-flight purchase fresh (title/subject) and poll again.
      setState({ phase: "generating", purchase });
      timer = setTimeout(poll, POLL_MS);
    };

    timer = setTimeout(poll, POLL_MS);
    return () => {
      cancelled = true;
      if (timer) clearTimeout(timer);
    };
  }, [generating, purchaseId]);

  async function submitRefine() {
    const message = refineText.trim();
    if (!message || refining) return;
    setRefining(true);
    setNotice(null);
    try {
      const r = await fetch(
        `/api/billing/questions/purchases/${purchaseId}/refine`,
        {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ message }),
        },
      );
      if (r.status === 401) return setState({ phase: "unauthorized" });
      if (r.status === 409) {
        const body = await r.json().catch(() => null);
        const detail = (body?.detail ?? body) as
          | { code?: string; suggested_slug?: string }
          | undefined;
        const code = detail?.code;
        if (
          code === "new_question" ||
          code === "window_exhausted" ||
          code === "refinement_unavailable"
        ) {
          setNotice({ kind: code, suggestedSlug: detail?.suggested_slug });
        } else {
          setNotice({ kind: "refinement_unavailable" });
        }
        return;
      }
      if (!r.ok) {
        setNotice({ kind: "refinement_unavailable" });
        return;
      }
      const purchase = (await r.json()) as QuestionPurchaseResponse;
      setState({ phase: "ready", purchase });
      setRefineText("");
    } finally {
      setRefining(false);
    }
  }

  if (state.phase === "loading") {
    // Brief pre-fetch beat before we know the purchase state — the shared
    // "ABS · READING" cursor (ABS-331) covers it.
    return (
      <div className="flex flex-col gap-3" data-testid="answer-status">
        <ReadingIndicator label="Loading your answer" />
        <div className="pl-7 sm:pl-8 text-text-muted text-[13px]">
          One moment.
        </div>
      </div>
    );
  }

  if (state.phase === "generating") {
    // ABS-343: the dedicated generation view — the report's own letterhead
    // + a six-step progress bar — plays before the finished ReportDocument.
    // It stays mounted until the poll effect above sees the background job
    // settle to `captured`, so the hand-off into the document is continuous.
    const title = state.purchase
      ? humanizeQuestionSlug(state.purchase.question_slug)
      : "Your bylaw report";
    return (
      <div data-testid="answer-status">
        <ReportGenerating title={title} />
      </div>
    );
  }

  if (state.phase === "unauthorized") {
    return (
      <Notice title="Sign in to view your answer">
        Your purchased answer lives behind your account.{" "}
        <Link href="/sign-in" className="underline text-text">
          Sign in
        </Link>
        .
      </Notice>
    );
  }

  if (state.phase === "notfound") {
    return (
      <Notice title="Answer not found">
        We couldn&apos;t find that purchase on your account. Head back to{" "}
        <Link href="/pricing" className="underline text-text">
          the question menu
        </Link>{" "}
        to buy an answer.
      </Notice>
    );
  }

  if (state.phase === "error") {
    return (
      <Notice title="Couldn't load your answer">
        {state.message} Please refresh the page to try again.
      </Notice>
    );
  }

  const { purchase } = state;
  const title = humanizeQuestionSlug(purchase.question_slug);

  // Failed question — voided/failed: no answer, no charge.
  if (purchase.status === "voided" || purchase.status === "failed") {
    const reason = purchase.failure_reason ?? "internal_error";
    return (
      <div className="flex flex-col gap-5" data-testid="answer-failed">
        <Header title={title} />
        <div className="bg-surface-alt border border-hair p-6">
          <div className="font-semibold mb-2">
            This question couldn&apos;t be answered
          </div>
          <div className="text-text-muted text-[13.5px]">
            {FAILURE_COPY[reason] ?? FAILURE_COPY.internal_error} You were
            not charged.
          </div>
          <div className="mt-4">
            <Link href="/pricing">
              <Btn variant="primary" size="sm">
                Back to the question menu
              </Btn>
            </Link>
          </div>
        </div>
      </div>
    );
  }

  // Defensive: a still-authorizing purchase (e.g. payment pending).
  if (purchase.status !== "captured") {
    return (
      <Notice title="Your answer isn't ready yet">
        This purchase is still being set up. Refresh in a moment to see
        your answer.
      </Notice>
    );
  }

  const remaining = purchase.refinements_remaining;
  const windowClosed = notice?.kind === "window_exhausted";
  const canRefine = remaining > 0 && !windowClosed;

  return (
    <div className="flex flex-col gap-6">
      {/* ABS-342: a captured report renders through the shared
          ReportDocument template (its own letterhead + title). Falls back
          to the raw engine markdown under a plain header if the structured
          report is somehow absent, so the deliverable never breaks. */}
      {purchase.report ? (
        <div data-testid="answer-body">
          <ReportDocument report={purchase.report} />
        </div>
      ) : (
        <>
          <Header title={title} />
          <article
            data-testid="answer-body"
            className="text-[14px] leading-[1.6]"
          >
            <AgentMarkdown source={purchase.answer ?? ""} />
          </article>
        </>
      )}

      <PaidAnswerDisclaimer />

      {/* Refinement window. */}
      <div className="border-t border-hair pt-6 flex flex-col gap-3">
        <div className="flex items-baseline justify-between">
          <Mono muted size={11}>
            REFINE THIS ANSWER
          </Mono>
          <span data-testid="refine-remaining">
            <Mono muted size={10}>
              {remaining} {remaining === 1 ? "FOLLOW-UP" : "FOLLOW-UPS"} LEFT
            </Mono>
          </span>
        </div>

        <div className="text-text-muted text-[12.5px]">
          Ask a clarifying follow-up about this answer — free, within the
          window. A materially different question (new property, new use)
          needs its own purchase.
        </div>

        {notice && <RefineNoticeCard notice={notice} />}

        {canRefine ? (
          <>
            <textarea
              data-testid="refine-input"
              value={refineText}
              onChange={(e) => setRefineText(e.target.value)}
              rows={3}
              placeholder="e.g. Summarize the key constraints in three bullet points."
              className="bg-surface border border-hair px-3 py-2 text-[13.5px] font-sans resize-y w-full"
            />
            <div>
              <Btn
                variant="accent"
                size="sm"
                onClick={submitRefine}
                disabled={refining || !refineText.trim()}
                data-testid="refine-submit"
              >
                {refining ? "Refining…" : "Send follow-up"}
              </Btn>
            </div>
          </>
        ) : (
          <div
            className="text-text-muted text-[12.5px]"
            data-testid="refine-closed"
          >
            {windowClosed
              ? "The refinement window for this answer is closed."
              : "You've used all the follow-ups on this answer."}{" "}
            <Link href="/pricing" className="underline text-text">
              Buy a new answer
            </Link>{" "}
            to keep going.
          </div>
        )}
      </div>
    </div>
  );
}

function RefineNoticeCard({ notice }: { notice: RefineNotice }) {
  if (notice.kind === "new_question") {
    return (
      <div
        data-testid="refine-notice"
        className="bg-surface-alt border border-hair p-4 text-[13px]"
      >
        <div className="font-semibold mb-1">
          That looks like a different question
        </div>
        <div className="text-text-muted">
          Answering it would be a separate bylaw report. Purchase a new
          question from{" "}
          <Link href="/pricing" className="underline text-text">
            the question menu
          </Link>{" "}
          to get a grounded answer for it.
        </div>
      </div>
    );
  }
  if (notice.kind === "window_exhausted") {
    return (
      <div
        data-testid="refine-notice"
        className="bg-surface-alt border border-hair p-4 text-[13px]"
      >
        <div className="font-semibold mb-1">Refinement window closed</div>
        <div className="text-text-muted">
          This answer&apos;s follow-up window is spent.{" "}
          <Link href="/pricing" className="underline text-text">
            Buy a new answer
          </Link>{" "}
          to ask more.
        </div>
      </div>
    );
  }
  return (
    <div
      data-testid="refine-notice"
      className="bg-surface-alt border border-hair p-4 text-[13px]"
    >
      <div className="font-semibold mb-1">Refinement unavailable</div>
      <div className="text-text-muted">
        This answer can&apos;t be refined right now.
      </div>
    </div>
  );
}

function Header({ title }: { title: string }) {
  return (
    <header className="flex flex-col gap-2.5 pb-5 border-b border-hair">
      <Mono muted size={11}>
        YOUR ANSWER
      </Mono>
      <h1
        className="font-sans font-extrabold m-0 text-[26px] sm:text-[32px] leading-[1.05]"
        style={{ letterSpacing: "-0.03em" }}
      >
        {title}
      </h1>
    </header>
  );
}

function Notice({
  title,
  children,
}: {
  title: string;
  children: React.ReactNode;
}) {
  return (
    <div className="bg-surface-alt border border-hair p-8">
      <div className="font-semibold mb-2">{title}</div>
      <div className="text-text-muted text-[13.5px]">{children}</div>
    </div>
  );
}
