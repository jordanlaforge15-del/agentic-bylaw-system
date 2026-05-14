// Marketing-page client component: posts to /api/billing/checkout/pack
// and redirects the browser to the returned Stripe URL.
//
// Lives in /components/marketing/ rather than /components/ because
// it's only used by the pricing page; the in-app product chrome has
// its own purchase flow.
//
// When the catalog says the offer isn't `available` (Stripe Price ID
// not configured for this SKU), the button renders disabled with a
// "coming soon" affordance. That keeps the pricing page usable
// during the pre-Stripe rollout.

"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";
import { Btn } from "@/components/btn";
import { PackSku, Tier } from "@/lib/cases";

type Props = {
  tier: Tier;
  packSku: PackSku;
  available: boolean;
  featured?: boolean;
};

export function BuyPackButton({ tier, packSku, available, featured }: Props) {
  const router = useRouter();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function onClick() {
    if (busy || !available) return;
    setBusy(true);
    setError(null);
    try {
      const r = await fetch("/api/billing/checkout/pack", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ tier, pack_sku: packSku }),
      });
      if (r.status === 401) {
        // Not signed in — redirect to login with a return-to so the
        // user lands back on /pricing afterwards.
        router.push(`/login?next=/pricing`);
        return;
      }
      if (!r.ok) {
        const detail = await r.json().catch(() => null);
        const code =
          detail && typeof detail === "object" && "detail" in detail
            ? (detail.detail as { message?: string })?.message
            : undefined;
        setError(code || `Checkout failed (${r.status}).`);
        return;
      }
      const data = (await r.json()) as { url?: string };
      if (data.url) {
        window.location.href = data.url;
        return;
      }
      setError("Checkout returned no URL. Try again.");
    } catch (e) {
      setError((e as Error).message || "Network error.");
    } finally {
      setBusy(false);
    }
  }

  if (!available) {
    return (
      <div className="flex flex-col gap-1">
        <Btn variant="quiet" size="sm" disabled>
          Coming soon
        </Btn>
        <span
          className={`text-[10.5px] ${featured ? "opacity-60" : "text-text-muted"}`}
          style={{ letterSpacing: "-0.005em" }}
        >
          Stripe checkout not yet configured for this SKU.
        </span>
      </div>
    );
  }

  return (
    <div className="flex flex-col gap-1.5">
      <Btn
        variant={featured ? "accent" : "primary"}
        size="sm"
        onClick={onClick}
        disabled={busy}
      >
        {busy ? "Opening checkout…" : "Buy"}
      </Btn>
      {error && (
        <span className="text-[11px] text-red-600">{error}</span>
      )}
    </div>
  );
}
