// /billing — logged-in account view under the marketing chrome. Renders
// the shared BillingContent (turn balance + wallet ledger + report
// history + cases) inside the marketing layout; /app/billing renders the
// same component inside the authorized shell (ABS-388).
//
// The tier/credit/pack vocabulary and the old server-side /v1/billing/me
// + /v1/billing/purchases fetches are gone: BillingContent client-fetches
// the turns-aware wallet, ledger, reports, and cases from the /api proxies
// and drives its own signed-out card.

import type { Metadata } from "next";
import { BillingContent } from "@/components/billing/billing-content";
import { Mono } from "@/components/mono";

export const dynamic = "force-dynamic";

// Account surface: unique title, noindex (see /cases for the rationale).
export const metadata: Metadata = {
  title: "Billing — ABS°",
  description:
    "Your ABS turn balance, wallet ledger, purchased reports, and case history in one account view. Top up turns whenever your balance runs low.",
  robots: { index: false, follow: false },
};

export default function BillingPage() {
  return (
    <div
      className="px-5 sm:px-8 py-10 sm:py-12 lg:py-14 mx-auto max-w-[1100px]"
      style={{ minHeight: "calc(100vh - 280px)" }}
    >
      <header className="flex flex-col gap-3 sm:gap-3.5 pb-6 sm:pb-7 mb-7 sm:mb-9 border-b border-hair">
        <Mono muted size={11}>
          ACCOUNT · TURNS
        </Mono>
        <h1
          className="font-sans font-extrabold m-0 text-[28px] sm:text-[36px] lg:text-[42px] leading-[1]"
          style={{ letterSpacing: "-0.04em" }}
        >
          Billing
        </h1>
      </header>

      <BillingContent signInHref="/login?next=/billing" />
    </div>
  );
}
