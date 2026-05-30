"use client";

import { useEffect, useState } from "react";
import { usePathname } from "next/navigation";
import { isProtectedPath } from "@/lib/protectedRoutes";
import { cn } from "@/lib/cn";

type Status = "idle" | "submitting" | "error";

/**
 * Global "Send feedback" widget (ABS-215).
 *
 * Rendered once from the root layout but only shows on authorized product
 * pages (see {@link isProtectedPath}), so logged-in users can send
 * unsolicited free-text feedback from anywhere in the app. The submission is
 * deliberately a single text box — no categories, tags, or multi-step form.
 * It posts to the same `/api/feedback` proxy as the categorized sidebar
 * widget; with no category supplied, the backend stores it as
 * `"uncategorized"` for downstream LLM categorization.
 */
export function GeneralFeedback() {
  const pathname = usePathname();
  const [open, setOpen] = useState(false);
  const [message, setMessage] = useState("");
  const [status, setStatus] = useState<Status>("idle");
  const [showThanks, setShowThanks] = useState(false);

  // Auto-dismiss the acknowledgment after 4s (parity with ABS-212).
  useEffect(() => {
    if (!showThanks) return;
    const timer = setTimeout(() => setShowThanks(false), 4000);
    return () => clearTimeout(timer);
  }, [showThanks]);

  // Only available on authorized (auth-gated) product pages.
  if (!pathname || !isProtectedPath(pathname)) {
    return null;
  }

  const trimmed = message.trim();

  async function submit() {
    if (!trimmed) return;
    setStatus("submitting");
    try {
      const res = await fetch("/api/feedback", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ message: trimmed }),
      });
      if (!res.ok) throw new Error("Request failed");
      setMessage("");
      setStatus("idle");
      setOpen(false);
      setShowThanks(true);
    } catch {
      setStatus("error");
    }
  }

  return (
    <div className="fixed right-4 bottom-4 z-50 flex flex-col items-end gap-2">
      {showThanks && (
        <div
          role="status"
          data-testid="app-feedback-thanks"
          className="border border-hair bg-surface-alt text-text px-3 py-2 text-[12px] font-mono shadow-lg"
        >
          Thanks for your feedback.
        </div>
      )}

      {open ? (
        <div
          role="dialog"
          aria-label="Feedback form"
          data-testid="app-feedback-form"
          className="w-80 border border-hair bg-surface-alt p-4 flex flex-col gap-2.5 shadow-xl"
        >
          <label
            htmlFor="app-feedback-message"
            className="font-mono uppercase text-text-muted"
            style={{ fontSize: 10, letterSpacing: "0.08em" }}
          >
            Share your feedback
          </label>
          <textarea
            id="app-feedback-message"
            data-testid="app-feedback-message"
            placeholder="Tell us what's working, what isn't, or what you'd like to see."
            value={message}
            onChange={(e) => setMessage(e.target.value)}
            rows={5}
            maxLength={2000}
            className="w-full border border-hair bg-surface text-text text-[12.5px] p-2 resize-y"
            style={{ minHeight: 96 }}
          />
          {status === "error" && (
            <p role="alert" className="text-[12px] text-danger m-0">
              Something went wrong. Please try again.
            </p>
          )}
          <div className="flex gap-2 justify-end">
            <button
              type="button"
              data-testid="app-feedback-cancel"
              onClick={() => {
                setOpen(false);
                setStatus("idle");
              }}
              disabled={status === "submitting"}
              className="font-mono uppercase text-[10.5px] px-3 py-1.5 border border-hair text-text-muted hover:text-text cursor-pointer"
              style={{ letterSpacing: "0.06em" }}
            >
              Cancel
            </button>
            <button
              type="button"
              data-testid="app-feedback-submit"
              onClick={submit}
              disabled={status === "submitting" || trimmed.length === 0}
              className={cn(
                "font-mono uppercase text-[10.5px] px-3 py-1.5 border transition-colors duration-100",
                trimmed.length > 0 && status !== "submitting"
                  ? "border-accent text-accent-ink hover:bg-accent/10 cursor-pointer"
                  : "border-hair text-text-muted cursor-not-allowed opacity-50",
              )}
              style={{ letterSpacing: "0.06em" }}
            >
              {status === "submitting" ? "Sending…" : "Send feedback"}
            </button>
          </div>
        </div>
      ) : (
        <button
          type="button"
          data-testid="app-feedback-open"
          aria-label="Send feedback"
          onClick={() => {
            setOpen(true);
            setShowThanks(false);
          }}
          className="font-mono uppercase text-[11px] tracking-wide px-4 py-2 border border-hair bg-surface text-text hover:bg-surface-alt cursor-pointer shadow-lg"
          style={{ letterSpacing: "0.06em" }}
        >
          Feedback
        </button>
      )}
    </div>
  );
}
