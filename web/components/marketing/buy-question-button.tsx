// Marketing-page client component: posts to /api/billing/checkout/question
// and redirects the browser to the returned hosted-checkout URL.
//
// When the catalog marks the question `available: false` (the payment
// processor's Price ID not configured, or billing dormant), the button
// renders disabled so the pricing page remains usable before checkout
// is fully wired.

"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";
import { Btn } from "@/components/btn";

type Props = {
  questionSlug: string;
  available: boolean;
};

export function BuyQuestionButton({ questionSlug, available }: Props) {
  const router = useRouter();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function onClick() {
    if (busy || !available) return;
    setBusy(true);
    setError(null);
    try {
      const r = await fetch("/api/billing/checkout/question", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ question_slug: questionSlug }),
      });
      if (r.status === 401) {
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
          className="text-[10.5px] text-text-muted"
          style={{ letterSpacing: "-0.005em" }}
        >
          Checkout not yet configured for this question.
        </span>
      </div>
    );
  }

  return (
    <div className="flex flex-col gap-1.5">
      <Btn variant="primary" size="sm" onClick={onClick} disabled={busy}>
        {busy ? "Opening checkout…" : "Get answer"}
      </Btn>
      {error && <span className="text-[11px] text-red-600">{error}</span>}
    </div>
  );
}
