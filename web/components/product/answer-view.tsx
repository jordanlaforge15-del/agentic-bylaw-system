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
  | { phase: "answering" }
  | { phase: "ready"; purchase: QuestionPurchaseResponse }
  | { phase: "unauthorized" }
  | { phase: "notfound" }
  | { phase: "error"; message: string };

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

export function AnswerView({ purchaseId }: { purchaseId: number }) {
  const [state, setState] = useState<LoadState>({ phase: "loading" });
  const [refineText, setRefineText] = useState("");
  const [refining, setRefining] = useState(false);
  const [notice, setNotice] = useState<RefineNotice | null>(null);
  // Guards React 18 StrictMode's double-mount from firing two /answer
  // POSTs (the run is idempotent upstream, but a double request races
  // two loading states).
  const ranAnswer = useRef(false);

  const runAnswer = useCallback(async () => {
    setState({ phase: "answering" });
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
    const purchase = (await r.json()) as QuestionPurchaseResponse;
    setState({ phase: "ready", purchase });
  }, [purchaseId]);

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
      // Not yet run → kick off the engine once.
      if (purchase.status === "authorized" && !ranAnswer.current) {
        ranAnswer.current = true;
        await runAnswer();
        return;
      }
      setState({ phase: "ready", purchase });
    })();
    return () => {
      cancelled = true;
    };
  }, [purchaseId, runAnswer]);

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

  if (state.phase === "loading" || state.phase === "answering") {
    // ABS-331: reinstate the app-page reading UX — the animated
    // "ABS · READING → Reading the bylaw…" cursor (shared with the /app
    // chat thread) instead of a static "Generating your answer" line.
    return (
      <div className="flex flex-col gap-3" data-testid="answer-status">
        <ReadingIndicator
          label={
            state.phase === "answering" ? "Reading the bylaw" : "Loading your answer"
          }
        />
        <div className="pl-7 sm:pl-8 text-text-muted text-[13px]">
          {state.phase === "answering"
            ? "The engine is grounding your answer in the bylaw. This can take a few seconds."
            : "One moment."}
        </div>
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
