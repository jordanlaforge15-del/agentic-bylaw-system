"use client";

import { useState } from "react";
import { Sheet } from "@/components/sheet";
import { Btn } from "@/components/btn";
import { Mono } from "@/components/mono";

const CATEGORIES = [
  { value: "ux_issue", label: "UX Issue" },
  { value: "feature_request", label: "Feature Request" },
  { value: "bug_report", label: "Bug Report" },
  { value: "general", label: "General" },
] as const;

const inputClass =
  "px-3.5 py-3 bg-surface text-text font-sans text-[14px] outline-none tracking-[-0.005em] " +
  "border-[1.5px] border-text focus:border-accent-ink";

type Props = {
  open: boolean;
  onClose: () => void;
};

export function FeedbackForm({ open, onClose }: Props) {
  const [category, setCategory] = useState("general");
  const [body, setBody] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [submitted, setSubmitted] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const reset = () => {
    setCategory("general");
    setBody("");
    setSubmitting(false);
    setSubmitted(false);
    setError(null);
  };

  const handleClose = () => {
    reset();
    onClose();
  };

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!body.trim()) return;

    setSubmitting(true);
    setError(null);

    try {
      const res = await fetch("/api/feedback", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ category, body: body.trim() }),
      });

      if (!res.ok) {
        const data = await res.json().catch(() => null);
        throw new Error(
          (data as { error?: string })?.error || `Request failed (${res.status})`,
        );
      }

      setSubmitted(true);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Something went wrong");
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <Sheet open={open} onClose={handleClose} ariaLabel="Send feedback">
      <div className="px-5 pt-3 pb-5 flex flex-col gap-4 overflow-y-auto">
        <div className="flex items-center justify-between">
          <Mono size={12}>FEEDBACK</Mono>
          <button
            type="button"
            onClick={handleClose}
            className="text-text-muted hover:text-text text-lg leading-none"
            aria-label="Close"
          >
            &times;
          </button>
        </div>

        {submitted ? (
          <div className="flex flex-col items-center gap-3 py-6" data-testid="feedback-success">
            <Mono size={11} muted>
              THANK YOU
            </Mono>
            <p className="text-sm text-text-muted text-center">
              Your feedback has been submitted.
            </p>
            <Btn variant="ghost" size="sm" onClick={handleClose}>
              Close
            </Btn>
          </div>
        ) : (
          <form onSubmit={handleSubmit} className="flex flex-col gap-4">
            <label className="flex flex-col gap-1.5">
              <Mono muted size={10}>
                CATEGORY
              </Mono>
              <select
                value={category}
                onChange={(e) => setCategory(e.target.value)}
                className={inputClass}
                data-testid="feedback-category"
              >
                {CATEGORIES.map((c) => (
                  <option key={c.value} value={c.value}>
                    {c.label}
                  </option>
                ))}
              </select>
            </label>

            <label className="flex flex-col gap-1.5">
              <Mono muted size={10}>
                YOUR FEEDBACK
              </Mono>
              <textarea
                value={body}
                onChange={(e) => setBody(e.target.value)}
                rows={4}
                required
                placeholder="Tell us what's on your mind..."
                className={`${inputClass} resize-y`}
                data-testid="feedback-body"
              />
            </label>

            {error && (
              <p className="text-sm text-brick" data-testid="feedback-error">
                {error}
              </p>
            )}

            <Btn
              variant="primary"
              size="md"
              type="submit"
              disabled={submitting || !body.trim()}
            >
              {submitting ? "Sending..." : "Submit Feedback"}
            </Btn>
          </form>
        )}
      </div>
    </Sheet>
  );
}
