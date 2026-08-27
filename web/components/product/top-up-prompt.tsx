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
import type {
  BetaRefillState,
  WalletRefillResponse,
  WalletResponse,
} from "@/lib/cases";

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

/** "in 4 hours" / "in 25 minutes" for a cooldown unlock instant.
 *
 * Deliberately coarse: the exact second is noise, and rounding up means we
 * never tell someone to come back before the claim would actually succeed. */
function untilLabel(iso: string): string | null {
  const target = new Date(iso).getTime();
  if (Number.isNaN(target)) return null;
  const minutes = Math.ceil((target - Date.now()) / 60_000);
  if (minutes <= 0) return null;
  if (minutes < 60) {
    return `in ${minutes} minute${minutes === 1 ? "" : "s"}`;
  }
  const hours = Math.ceil(minutes / 60);
  return `in ${hours} hour${hours === 1 ? "" : "s"}`;
}

export function TopUpPrompt({
  paymentsEnabled,
  sku = "small",
  betaRefill,
  onRefilled,
}: Props) {
  const [working, setWorking] = useState(false);
  const [error, setError] = useState<string | null>(null);
  // Set when a claim comes back refused — the server is the authority on the
  // cooldown, so a stale "available" in our props gets corrected here rather
  // than leaving the button looking live.
  const [refusal, setRefusal] = useState<string | null>(null);

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

  async function claimRefill() {
    if (working) return;
    setWorking(true);
    setError(null);
    setRefusal(null);
    try {
      const r = await fetch("/api/billing/wallet/refill", { method: "POST" });
      if (!r.ok) {
        setError("Couldn't add turns right now. Please try again shortly.");
        return;
      }
      const data = (await r.json()) as WalletRefillResponse;
      if (data.status === "granted") {
        // The response carries the post-claim wallet, so the shell flips out
        // of the out-of-turns state without a second read.
        onRefilled?.(data.wallet);
        return;
      }
      // Refused after the fact — usually a second click that raced the first.
      const next = data.wallet?.beta_refill?.next_available_at;
      const when = next ? untilLabel(next) : null;
      setRefusal(
        data.status === "cooldown" && when
          ? `More turns unlock ${when}.`
          : "No more turns are available on this account right now.",
      );
    } catch {
      setError("Couldn't add turns right now. Please try again shortly.");
    } finally {
      setWorking(false);
    }
  }

  const refillAvailable = !paymentsEnabled && betaRefill?.available === true;
  const cooldownUntil =
    !paymentsEnabled && betaRefill?.status === "cooldown"
      ? untilLabel(betaRefill.next_available_at ?? "")
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
      ) : refillAvailable ? (
        <>
          <div className="text-[14px]" data-testid="beta-refill-offer">
            You&rsquo;re out of turns. Paid top-ups are coming soon — until
            then you can add {betaRefill.approx_turns === 1
              ? "another turn"
              : `${betaRefill.approx_turns} more turns`}{" "}
            to this account yourself.
          </div>
          <div>
            <Btn
              variant="accent"
              size="sm"
              onClick={claimRefill}
              disabled={working}
              data-testid="beta-refill-btn"
            >
              {working ? "Adding turns…" : "Add more turns →"}
            </Btn>
          </div>
          {refusal && (
            <div
              className="text-[12.5px] text-text-muted"
              data-testid="beta-refill-refusal"
            >
              {refusal}
            </div>
          )}
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
          You&rsquo;re out of turns for now — paid top-ups are coming soon.
          {cooldownUntil ? ` More turns unlock ${cooldownUntil}.` : ""} Your
          case and its full history stay saved, so you can pick up right where
          you left off.
        </div>
      )}
    </div>
  );
}
