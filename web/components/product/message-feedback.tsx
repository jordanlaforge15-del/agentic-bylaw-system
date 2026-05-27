"use client";

import { useCallback, useEffect, useState } from "react";
import { cn } from "@/lib/cn";

type Rating = "up" | "down" | null;
type FlagReason = "bad_citation" | "wrong_zone" | "hallucination" | "other" | null;
type ToastType = "thumbs" | "flag" | null;

type Props = {
  sessionId: string;
  messageId: number;
};

const FLAG_OPTIONS: { value: FlagReason & string; label: string }[] = [
  { value: "bad_citation", label: "Bad citation" },
  { value: "wrong_zone", label: "Wrong zone" },
  { value: "hallucination", label: "Hallucination" },
  { value: "other", label: "Other" },
];

export function MessageFeedback({ sessionId, messageId }: Props) {
  const [rating, setRating] = useState<Rating>(null);
  const [flagOpen, setFlagOpen] = useState(false);
  const [flagOpenedViaThumbsDown, setFlagOpenedViaThumbsDown] = useState(false);
  const [flagReason, setFlagReason] = useState<FlagReason>(null);
  const [flagNotes, setFlagNotes] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [flagSubmitted, setFlagSubmitted] = useState(false);
  const [toastType, setToastType] = useState<ToastType>(null);

  useEffect(() => {
    if (!toastType) return;
    const timer = setTimeout(() => setToastType(null), 2500);
    return () => clearTimeout(timer);
  }, [toastType]);

  const submitFeedback = useCallback(
    async (payload: {
      rating?: Rating;
      flag_reason?: FlagReason;
      flag_notes?: string;
      toastType?: ToastType;
    }) => {
      setSubmitting(true);
      try {
        await fetch(
          `/api/chat/sessions/${encodeURIComponent(sessionId)}/messages/${messageId}/feedback`,
          {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
              rating: payload.rating !== undefined ? payload.rating : rating,
              flag_reason: payload.flag_reason ?? flagReason,
              flag_notes: payload.flag_notes ?? (flagNotes || null),
            }),
          },
        );
        if (payload.toastType) {
          setToastType(payload.toastType);
        }
      } catch {
        // Silently degrade — feedback is non-critical.
      } finally {
        setSubmitting(false);
      }
    },
    [sessionId, messageId, rating, flagReason, flagNotes],
  );

  const handleThumb = (value: Rating) => {
    const next = rating === value ? null : value;
    setRating(next);
    if (next === "down") {
      // Auto-open the flag panel so user can explain why
      setFlagOpen(true);
      setFlagOpenedViaThumbsDown(true);
      void submitFeedback({ rating: next }); // toast deferred until panel submit/skip
    } else {
      if (value === "down") {
        // Un-toggling thumbs-down — close the auto-opened panel
        setFlagOpen(false);
        setFlagOpenedViaThumbsDown(false);
      }
      void submitFeedback({ rating: next, toastType: "thumbs" });
    }
  };

  const handleFlagSubmit = () => {
    if (!flagOpenedViaThumbsDown && !flagReason) return;
    setFlagSubmitted(true);
    setFlagOpen(false);
    setFlagOpenedViaThumbsDown(false);
    void submitFeedback({
      flag_reason: flagReason ?? undefined,
      flag_notes: flagNotes || undefined,
      toastType: "flag",
    });
  };

  const handleFlagCancel = () => {
    setFlagOpen(false);
    setFlagOpenedViaThumbsDown(false);
  };

  return (
    <div className="flex flex-col gap-2" data-testid="message-feedback">
      <div className="flex items-center gap-1.5">
        {toastType === "thumbs" && (
          <div
            data-testid="feedback-toast-thumbs"
            className="text-[11px] font-mono text-text-muted ml-auto"
          >
            Thanks — feedback recorded.
          </div>
        )}
        <button
          type="button"
          data-testid="feedback-thumbs-up"
          disabled={submitting}
          onClick={() => handleThumb("up")}
          className={cn(
            "inline-flex items-center justify-center border cursor-pointer",
            "transition-colors duration-100",
            rating === "up"
              ? "border-accent bg-accent/10 text-accent-ink"
              : "border-hair bg-transparent text-text-muted hover:text-text hover:border-text-muted",
          )}
          style={{ width: 28, height: 28, fontSize: 13 }}
          aria-label="Thumbs up"
          aria-pressed={rating === "up"}
        >
          <ThumbUpIcon />
        </button>

        <button
          type="button"
          data-testid="feedback-thumbs-down"
          disabled={submitting}
          onClick={() => handleThumb("down")}
          className={cn(
            "inline-flex items-center justify-center border cursor-pointer",
            "transition-colors duration-100",
            rating === "down"
              ? "border-[var(--brick)] bg-[var(--brick)]/10 text-[var(--brick)]"
              : "border-hair bg-transparent text-text-muted hover:text-text hover:border-text-muted",
          )}
          style={{ width: 28, height: 28, fontSize: 13 }}
          aria-label="Thumbs down"
          aria-pressed={rating === "down"}
        >
          <ThumbDownIcon />
        </button>

        <button
          type="button"
          data-testid="feedback-flag-btn"
          disabled={submitting}
          onClick={() => {
            setFlagOpen((o) => !o);
            setFlagOpenedViaThumbsDown(false);
          }}
          className={cn(
            "inline-flex items-center justify-center border cursor-pointer",
            "transition-colors duration-100",
            flagSubmitted
              ? "border-accent bg-accent/10 text-accent-ink"
              : "border-hair bg-transparent text-text-muted hover:text-text hover:border-text-muted",
          )}
          style={{ width: 28, height: 28, fontSize: 13 }}
          aria-label="Flag this response"
        >
          <FlagIcon />
        </button>
      </div>

      {flagOpen && (
        <div
          data-testid="flag-panel"
          className="border border-hair bg-surface-alt p-3 flex flex-col gap-2.5"
          style={{ maxWidth: 340 }}
        >
          <span
            className="font-mono uppercase text-text-muted"
            style={{ fontSize: 10, letterSpacing: "0.08em" }}
          >
            {flagOpenedViaThumbsDown ? "What went wrong?" : "Report an issue"}
          </span>

          <div className="flex flex-wrap gap-1.5">
            {FLAG_OPTIONS.map((opt) => (
              <button
                key={opt.value}
                type="button"
                data-testid={`flag-reason-${opt.value}`}
                onClick={() => setFlagReason(opt.value)}
                className={cn(
                  "font-mono text-[10.5px] uppercase px-2 py-1 border cursor-pointer",
                  "transition-colors duration-100",
                  flagReason === opt.value
                    ? "border-accent bg-accent/10 text-accent-ink"
                    : "border-hair text-text-muted hover:text-text",
                )}
                style={{ letterSpacing: "0.06em" }}
              >
                {opt.label}
              </button>
            ))}
          </div>

          <textarea
            data-testid="flag-notes"
            placeholder={flagOpenedViaThumbsDown ? "What went wrong?" : "Optional detail..."}
            value={flagNotes}
            onChange={(e) => setFlagNotes(e.target.value)}
            className="w-full border border-hair bg-surface text-text text-[12.5px] p-2 resize-none"
            style={{ minHeight: 56 }}
            maxLength={2000}
          />

          <div className="flex flex-col gap-1.5">
            <div className="flex gap-2">
              <div className="flex items-center gap-2">
                <button
                  type="button"
                  data-testid="flag-submit"
                  disabled={(flagOpenedViaThumbsDown ? false : !flagReason) || submitting}
                  onClick={handleFlagSubmit}
                  className={cn(
                    "font-mono uppercase text-[10.5px] px-3 py-1.5 border",
                    "transition-colors duration-100",
                    flagOpenedViaThumbsDown || flagReason
                      ? "border-accent text-accent-ink hover:bg-accent/10 cursor-pointer"
                      : "border-hair text-text-muted cursor-not-allowed opacity-50",
                  )}
                  style={{ letterSpacing: "0.06em" }}
                >
                  Submit
                </button>
                {flagSubmitted && (
                  <span
                    data-testid="flag-saved-indicator"
                    className="text-[10.5px] font-mono text-text-muted"
                  >
                    Saved.
                  </span>
                )}
              </div>
            <button
              type="button"
              data-testid="flag-cancel"
              onClick={handleFlagCancel}
              className="font-mono uppercase text-[10.5px] px-3 py-1.5 border border-hair text-text-muted hover:text-text cursor-pointer"
              style={{ letterSpacing: "0.06em" }}
            >
              {flagOpenedViaThumbsDown ? "Skip" : "Cancel"}
            </button>
          </div>
        </div>
      </div>
      )}

      {toastType === "flag" && (
        <div
          data-testid="feedback-toast-flag"
          className="text-[11px] font-mono text-text-muted"
        >
          Thanks — feedback recorded.
        </div>
      )}
    </div>
  );
}

function ThumbUpIcon() {
  return (
    <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
      <path d="M7 10v12" />
      <path d="M15 5.88 14 10h5.83a2 2 0 0 1 1.92 2.56l-2.33 8A2 2 0 0 1 17.5 22H4a2 2 0 0 1-2-2v-8a2 2 0 0 1 2-2h2.76a2 2 0 0 0 1.79-1.11L12 2a3.13 3.13 0 0 1 3 3.88Z" />
    </svg>
  );
}

function ThumbDownIcon() {
  return (
    <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
      <path d="M17 14V2" />
      <path d="M9 18.12 10 14H4.17a2 2 0 0 1-1.92-2.56l2.33-8A2 2 0 0 1 6.5 2H20a2 2 0 0 1 2 2v8a2 2 0 0 1-2 2h-2.76a2 2 0 0 0-1.79 1.11L12 22a3.13 3.13 0 0 1-3-3.88Z" />
    </svg>
  );
}

function FlagIcon() {
  return (
    <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
      <path d="M4 15s1-1 4-1 5 2 8 2 4-1 4-1V3s-1 1-4 1-5-2-8-2-4 1-4 1z" />
      <line x1="4" x2="4" y1="22" y2="15" />
    </svg>
  );
}
