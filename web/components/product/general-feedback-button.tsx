"use client";

import { useState, useEffect } from "react";
import { cn } from "@/lib/cn";

type Category = "ux_issue" | "feature_request" | "general_satisfaction" | "other";

const CATEGORIES: { value: Category; label: string }[] = [
  { value: "ux_issue", label: "UX issue" },
  { value: "feature_request", label: "Feature request" },
  { value: "general_satisfaction", label: "General" },
  { value: "other", label: "Other" },
];

export function GeneralFeedbackButton() {
  const [open, setOpen] = useState(false);
  const [category, setCategory] = useState<Category | null>(null);
  const [message, setMessage] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [submitted, setSubmitted] = useState(false);

  useEffect(() => {
    if (submitted) {
      const timeoutId = setTimeout(() => {
        setSubmitted(false);
      }, 4000);
      return () => clearTimeout(timeoutId);
    }
  }, [submitted]);

  const handleOpen = () => {
    setOpen(true);
    setSubmitted(false);
    setCategory(null);
    setMessage("");
  };

  const handleCancel = () => {
    setOpen(false);
  };

  const handleSubmit = async () => {
    if (!category || !message.trim()) return;
    setSubmitting(true);
    try {
      await fetch("/api/feedback", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ category, message: message.trim() }),
      });
      setSubmitted(true);
      setOpen(false);
    } catch {
      // Silently degrade — feedback is non-critical.
    } finally {
      setSubmitting(false);
    }
  };

  if (submitted) {
    return (
      <div
        data-testid="general-feedback-thanks"
        className="text-[11px] font-mono text-text-muted px-1"
      >
        Thanks for your feedback.
      </div>
    );
  }

  return (
    <div className="w-full" data-testid="general-feedback-widget">
      {!open && (
        <button
          type="button"
          data-testid="general-feedback-open"
          onClick={handleOpen}
          className="text-[11px] font-mono uppercase text-text-muted hover:text-text cursor-pointer bg-transparent border-0 px-1 py-0.5 tracking-wide transition-colors"
          style={{ letterSpacing: "0.06em" }}
        >
          Give feedback
        </button>
      )}

      {open && (
        <div
          data-testid="general-feedback-form"
          className="border border-hair bg-surface-alt p-3 flex flex-col gap-2.5"
        >
          <span
            className="font-mono uppercase text-text-muted"
            style={{ fontSize: 10, letterSpacing: "0.08em" }}
          >
            Share feedback
          </span>

          <div className="flex flex-wrap gap-1.5">
            {CATEGORIES.map((opt) => (
              <button
                key={opt.value}
                type="button"
                data-testid={`feedback-category-${opt.value}`}
                onClick={() => setCategory(opt.value)}
                className={cn(
                  "font-mono text-[10.5px] uppercase px-2 py-1 border cursor-pointer",
                  "transition-colors duration-100",
                  category === opt.value
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
            data-testid="general-feedback-message"
            placeholder="Tell us more..."
            value={message}
            onChange={(e) => setMessage(e.target.value)}
            className="w-full border border-hair bg-surface text-text text-[12.5px] p-2 resize-none"
            style={{ minHeight: 64 }}
            maxLength={2000}
          />

          <div className="flex gap-2">
            <button
              type="button"
              data-testid="general-feedback-submit"
              disabled={!category || !message.trim() || submitting}
              onClick={handleSubmit}
              className={cn(
                "font-mono uppercase text-[10.5px] px-3 py-1.5 border cursor-pointer",
                "transition-colors duration-100",
                category && message.trim()
                  ? "border-accent text-accent-ink hover:bg-accent/10"
                  : "border-hair text-text-muted cursor-not-allowed",
              )}
              style={{ letterSpacing: "0.06em" }}
            >
              Submit
            </button>
            <button
              type="button"
              data-testid="general-feedback-cancel"
              onClick={handleCancel}
              className="font-mono uppercase text-[10.5px] px-3 py-1.5 border border-hair text-text-muted hover:text-text cursor-pointer"
              style={{ letterSpacing: "0.06em" }}
            >
              Cancel
            </button>
          </div>
        </div>
      )}
    </div>
  );
}
