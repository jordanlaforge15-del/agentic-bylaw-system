// /pricing — "Pay by the turn" (ABS-387).
//
// Sells the conversation: a free-trial grant plus paid token top-ups, with
// fixed-price written reports as a secondary section gated by the per-report
// gate (ABS-384). The header copy is static; everything data-driven (top-up
// SKUs, report SKUs, payment posture) is fetched client-side by <PricingCards>
// through the Next proxy so the gate/posture subsets are stubbable and the
// page can retry a failed catalog load without a full navigation.

import { PricingCards } from "@/components/marketing/pricing-cards";
import { HighlightWord } from "@/components/highlight-word";
import { Mono } from "@/components/mono";

export const dynamic = "force-dynamic";

export default function PricingPage() {
  return (
    <div
      className="px-5 sm:px-8 py-10 sm:py-12 lg:py-14 mx-auto max-w-[1200px]"
      style={{ minHeight: "calc(100vh - 280px)" }}
    >
      <header className="flex flex-col gap-3 sm:gap-3.5 pb-6 sm:pb-7 mb-7 sm:mb-9 lg:mb-10 border-b border-hair">
        <Mono muted size={11}>
          PRICING · TURNS
        </Mono>
        <h1
          className="font-sans font-extrabold m-0 text-[36px] sm:text-[44px] lg:text-[56px] leading-[1] lg:leading-[0.98]"
          style={{ letterSpacing: "-0.04em" }}
        >
          Pay by the <HighlightWord>turn.</HighlightWord>
        </h1>
        <p className="text-[14px] sm:text-[16px] lg:text-[17px] text-text-muted leading-[1.45] m-0 max-w-[680px]">
          Sign up and start with free trial turns. Opening a case is free —
          replies draw on your balance. Top up whenever you need more. All
          prices in CAD.
        </p>
      </header>

      <PricingCards />
    </div>
  );
}
