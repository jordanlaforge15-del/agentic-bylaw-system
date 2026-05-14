// Inline upgrade prompt rendered above the composer when the agent
// fires a `case_upgrade_offer` event via the request_tier_upgrade
// tool (Layer 3 mid-session enforcement).
//
// Two paths:
//
//   1. User has an available higher-tier credit → POST
//      /api/cases/{id}/upgrade. The credit swap is atomic; the
//      backend returns the updated case + new credit id, and the
//      session continues seamlessly.
//
//   2. No higher-tier credit available → backend returns 409. We show
//      a "Buy a credit" CTA that punts to /pricing (the user's case
//      stays open while they purchase).

"use client";

import { useState } from "react";
import { Btn } from "@/components/btn";
import { Mono } from "@/components/mono";

type Offer = {
  case_id: number;
  current_tier: string;
  recommended_tier: string;
  reason: string;
};

type Props = {
  offer: Offer;
  onClose: () => void;
  onAccepted: (newTier: string) => void;
};

const TIER_DISPLAY: Record<string, string> = {
  quick: "Quick Lookup",
  standard: "Standard Case",
  complex: "Complex File",
};


export function CaseUpgradePrompt({ offer, onClose, onAccepted }: Props) {
  const [working, setWorking] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [needsPurchase, setNeedsPurchase] = useState(false);

  async function accept() {
    if (working) return;
    setWorking(true);
    setError(null);
    setNeedsPurchase(false);
    try {
      const r = await fetch(
        `/api/cases/${offer.case_id}/upgrade`,
        {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            target_tier: offer.recommended_tier,
            trigger: "agent_request",
          }),
        },
      );
      if (r.status === 409) {
        setNeedsPurchase(true);
        return;
      }
      if (!r.ok) {
        const detail = await r.json().catch(() => null);
        const msg =
          (detail?.detail as { message?: string } | undefined)?.message ??
          `Upgrade failed (${r.status}).`;
        setError(msg);
        return;
      }
      onAccepted(offer.recommended_tier);
    } finally {
      setWorking(false);
    }
  }

  return (
    <div className="border-t border-hair bg-surface-alt p-4 flex flex-col gap-3">
      <div>
        <Mono size={11} muted>
          AGENT REQUESTS UPGRADE
        </Mono>
        <div className="text-[14px] mt-1">
          Continuing thoroughly needs the{" "}
          <strong>{TIER_DISPLAY[offer.recommended_tier]}</strong> budget.
        </div>
        <div className="text-[12.5px] text-text-muted mt-1">
          {offer.reason}
        </div>
      </div>

      {needsPurchase ? (
        <div className="text-[12.5px] text-text-muted">
          You don&apos;t have an available{" "}
          {TIER_DISPLAY[offer.recommended_tier]} credit.{" "}
          <a className="underline" href="/pricing">
            Buy one
          </a>{" "}
          to continue (your case stays open).
        </div>
      ) : (
        error && <div className="text-[12.5px] text-red-600">{error}</div>
      )}

      <div className="flex gap-2">
        <Btn
          variant="accent"
          size="sm"
          onClick={accept}
          disabled={working || needsPurchase}
        >
          {working
            ? "Upgrading…"
            : `Upgrade to ${TIER_DISPLAY[offer.recommended_tier]}`}
        </Btn>
        <Btn variant="quiet" size="sm" onClick={onClose} disabled={working}>
          Stay at {TIER_DISPLAY[offer.current_tier]}
        </Btn>
      </div>
    </div>
  );
}
