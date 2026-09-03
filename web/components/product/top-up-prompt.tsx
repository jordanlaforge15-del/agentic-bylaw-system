// Out-of-turns prompt rendered above the disabled composer when the account
// token wallet is at or below the floor (ABS-386). Replaces the retired
// CaseUpgradePrompt (the tier/credit machinery is gone).
//
// Three postures:
//
//   * payments ON  → a purchase CTA that starts a top-up checkout
//     (POST /api/billing/checkout/topup → Stripe URL, MockStripe in e2e) and
//     redirects. On return, the /app mount refetches the wallet and the
//     composer re-enables.
//   * payments OFF, refill available (ABS-405) → a self-serve claim CTA. One
//     POST to /api/billing/wallet/refill credits a capped, cooldown-gated
//     grant and hands back the post-claim wallet, so the composer re-enables
//     in place with no reload and no support ticket. This is the whole point
//     of the feature: before it, an overdrawn beta tester's only way back
//     into chat was an operator running grant_tokens by hand.
//   * payments OFF, no refill (cooldown / cap spent / feature off) → a kind
//     dead-end: no CTA, reassurance that the case and its history stay
//     saved. On cooldown we say *when* more turns unlock rather than a bare
//     "no", so the user knows to come back instead of emailing support.
//
// Copy is turns-based ("You're out of turns…") — never token counts, never
// tier vocabulary. Attention uses the BRICK colour + alarm glyph (never
// colour alone); the lime ACCENT is reserved for the positive actions
// (top up, claim a refill).

"use client";

import { useState } from "react";
import { Btn } from "@/components/btn";
import { Mono } from "@/components/mono";
import {
  BetaRefillClaim,
  refillTurnsLabel,
  refillUnlockLabel,
} from "@/components/product/beta-refill";
import type { BetaRefillState, WalletResponse } from "@/lib/cases";

type Props = {
  paymentsEnabled: boolean;
  /** Default top-up SKU to start checkout with. */
  sku?: string;
  /**
   * ABS-405 refill availability, straight off the wallet read. Undefined
   * when the backend predates the feature — treated as "no refill", which
   * lands on the original dead-end copy.
   */
  betaRefill?: BetaRefillState;
  /** Called with the post-claim wallet so the shell re-enables the composer. */
  onRefilled?: (wallet: WalletResponse) => void;
};

export function TopUpPrompt({
  paymentsEnabled,
  sku = "small",
  betaRefill,
  onRefilled,
}: Props) {
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

  const refillAvailable = !paymentsEnabled && betaRefill?.available === true;
  const cooldownUntil =
    !paymentsEnabled && betaRefill?.status === "cooldown"
      ? refillUnlockLabel(betaRefill.next_available_at)
      : null;

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
      ) : refillAvailable && betaRefill ? (
        <>
          <div className="text-[14px]" data-testid="beta-refill-offer">
            You&rsquo;re out of turns. Paid top-ups are coming soon — until
            then you can add {refillTurnsLabel(betaRefill)} to this account
            yourself.
          </div>
          <BetaRefillClaim onRefilled={(w) => onRefilled?.(w)} />
        </>
      ) : (
        <div
          className="text-[12.5px] text-text-muted max-w-[440px]"
          data-testid="top-up-deadend"
        >
          You&rsquo;re out of turns for now — paid top-ups are coming soon.
          {cooldownUntil ? ` More turns unlock ${cooldownUntil}.` : ""} Your
          case and its full history stay saved, so you can pick up right where
          you left off.
        </div>
      )}
    </div>
  );
}
