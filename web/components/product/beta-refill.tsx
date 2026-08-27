// Self-serve beta refill claim (ABS-405) — the shared piece behind every
// "you're out of turns" surface.
//
// While payments are off there is nothing a user can buy, so an exhausted
// wallet used to be a hard stop: the only way back in was an operator
// running grant_tokens by hand, and every stuck tester became a support
// touch. The backend hands a capped, cooldown-gated grant to anyone who
// asks (POST /api/billing/wallet/refill); this is the button that asks.
//
// It lives in its own module because the exhaustion state is rendered in
// three places — the chat out-of-turns prompt, the case-open balance
// notice, and the billing page — and a fix that only reached the first one
// would leave two dead ends for the same stuck user. Surfaces supply their
// own surrounding copy; the claim mechanics are here, once.

"use client";

import { useState } from "react";
import { Btn } from "@/components/btn";
import type {
  BetaRefillState,
  WalletRefillResponse,
  WalletResponse,
} from "@/lib/cases";

/** "in 4 hours" / "in 25 minutes" for a cooldown unlock instant.
 *
 * Deliberately coarse: the exact second is noise, and rounding up means we
 * never tell someone to come back before the claim would actually succeed.
 * Returns null for a past or unparseable instant, so callers fall back to
 * copy that doesn't promise a time. */
export function refillUnlockLabel(iso: string | null | undefined): string | null {
  if (!iso) return null;
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

/** Is the claim on offer? Payments-on is excluded here rather than at each
 * call site: once there is something to buy, top-up is the path and the
 * backend refuses the claim outright — the UI must agree. */
export function canClaimRefill(
  wallet: Pick<WalletResponse, "payments_enabled" | "beta_refill"> | null,
): boolean {
  if (!wallet || wallet.payments_enabled) return false;
  return wallet.beta_refill?.available === true;
}

/** "another turn" / "3 more turns" — the size of one claim, in the only
 * unit the product speaks. The figure is backend-owned like every other
 * turn count; we never divide tokens here. */
export function refillTurnsLabel(betaRefill: BetaRefillState): string {
  return betaRefill.approx_turns === 1
    ? "another turn"
    : `${betaRefill.approx_turns} more turns`;
}

type Props = {
  /** Called with the post-claim wallet, which the caller should adopt as
   * its new wallet state — it already reflects the credited balance. */
  onRefilled: (wallet: WalletResponse) => void;
  label?: string;
  testId?: string;
};

export function BetaRefillClaim({
  onRefilled,
  label = "Add more turns →",
  testId = "beta-refill-btn",
}: Props) {
  const [working, setWorking] = useState(false);
  const [error, setError] = useState<string | null>(null);
  // Set when a claim comes back refused. The server is the authority on the
  // cooldown, so a stale "available" in our props gets corrected here rather
  // than leaving the button looking live.
  const [refusal, setRefusal] = useState<string | null>(null);

  async function claim() {
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
        onRefilled(data.wallet);
        return;
      }
      // Refused after the fact — usually a second click that raced the first.
      const when = refillUnlockLabel(data.wallet?.beta_refill?.next_available_at);
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

  return (
    <>
      <div>
        <Btn
          variant="accent"
          size="sm"
          onClick={claim}
          disabled={working}
          data-testid={testId}
          className="whitespace-nowrap"
        >
          {working ? "Adding turns…" : label}
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
        <div className="text-[12.5px] text-brick" data-testid="beta-refill-error">
          {error}
        </div>
      )}
    </>
  );
}
