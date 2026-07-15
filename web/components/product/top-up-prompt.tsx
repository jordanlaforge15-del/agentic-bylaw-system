// Out-of-turns prompt rendered above the disabled composer when the account
// token wallet is at or below the floor (ABS-386). Replaces the retired
// CaseUpgradePrompt (the tier/credit machinery is gone).
//
// Two postures, matching the /cases/new empty-notice variants:
//
//   * payments ON  → a purchase CTA that starts a top-up checkout
//     (POST /api/billing/checkout/topup → Stripe URL, MockStripe in e2e) and
//     redirects. On return, the /app mount refetches the wallet and the
//     composer re-enables.
//   * payments OFF → a kind dead-end: no purchase CTA, reassurance that the
//     case and its history stay saved (trial-exhausted, "coming soon").
//
// Copy is turns-based ("You're out of turns…") — never token counts, never
// tier vocabulary. Attention uses the BRICK colour + alarm glyph (never
// colour alone); the lime ACCENT is reserved for the positive top-up action.

"use client";

import { useState } from "react";
import { Btn } from "@/components/btn";
import { Mono } from "@/components/mono";

type Props = {
  paymentsEnabled: boolean;
  /** Default top-up SKU to start checkout with. */
  sku?: string;
};

export function TopUpPrompt({ paymentsEnabled, sku = "small" }: Props) {
  const [working, setWorking] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function startTopUp() {
    if (working) return;
    setWorking(true);
    setError(null);
    try {
      const r = await fetch("/api/billing/checkout/topup", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ sku }),
      });
      if (!r.ok) {
        setError("Top-up isn't available right now. Please try again shortly.");
        return;
      }
      const data = (await r.json()) as { url?: string };
      if (data.url && typeof window !== "undefined") {
        window.location.href = data.url;
        return;
      }
      setError("Top-up isn't available right now. Please try again shortly.");
    } catch {
      setError("Top-up isn't available right now. Please try again shortly.");
    } finally {
      setWorking(false);
    }
  }

  return (
    <div
      data-testid="top-up-prompt"
      role="status"
      aria-live="polite"
      className="border-t border-hair bg-surface-alt p-4 flex flex-col gap-2"
    >
      <div className="flex items-center gap-1.5">
        <span aria-hidden className="text-brick font-bold leading-none">
          ▲
        </span>
        <Mono size={11} className="text-brick">
          OUT OF TURNS
        </Mono>
      </div>

      {paymentsEnabled ? (
        <>
          <div className="text-[14px]">
            You&rsquo;re out of turns. Top up to keep asking questions on this
            case.
          </div>
          <div>
            <Btn
              variant="accent"
              size="sm"
              onClick={startTopUp}
              disabled={working}
              data-testid="top-up-btn"
            >
              {working ? "Opening checkout…" : "Top up →"}
            </Btn>
          </div>
          {error && (
            <div className="text-[12.5px] text-brick" data-testid="top-up-error">
              {error}
            </div>
          )}
        </>
      ) : (
        <div
          className="text-[12.5px] text-text-muted max-w-[440px]"
          data-testid="top-up-deadend"
        >
          You&rsquo;re out of turns for now — paid top-ups are coming soon. Your
          case and its full history stay saved, so you can pick up right where
          you left off.
        </div>
      )}
    </div>
  );
}
