// /app/billing — in-app account & billing view. Renders the shared
// BillingContent (turn balance + wallet ledger + report history + cases)
// inside the authorized shell (AuthBar + workspace menu + AuthFooter), so
// the user keeps the in-app chrome. Same content as /billing (the
// marketing-chrome billing page) — one component, two shells (ABS-388).

import { BillingContent } from "@/components/billing/billing-content";
import { Mono } from "@/components/mono";
import { AuthShell } from "@/components/product/auth-shell";

export const dynamic = "force-dynamic";

export default function AppBillingPage() {
  return (
    <AuthShell>
      <div className="px-5 sm:px-8 py-12 sm:py-14 mx-auto max-w-[1100px]">
        <header className="flex flex-col gap-3 sm:gap-4 pb-7 sm:pb-9 mb-7 sm:mb-9 border-b border-hair">
          <Mono muted size={10}>
            ACCOUNT · TURNS
          </Mono>
          <h1
            className="font-sans font-extrabold m-0 text-[40px] sm:text-[56px] leading-none"
            style={{ letterSpacing: "-0.04em" }}
          >
            Billing.
          </h1>
        </header>

        <BillingContent signInHref="/sign-in" />
      </div>
    </AuthShell>
  );
}
