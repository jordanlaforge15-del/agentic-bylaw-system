// /cases/new — "Open a case" (ABS-334). Per-answer entry flow rendered
// inside the authorized shell (AuthBar + AuthFooter), not the marketing
// chrome. Reached from the /app sidebar's "+ Open a case" action and the
// workspace menu. The interactive form (anchor, priced question catalog,
// intake, checkout) lives in the OpenCaseForm client component.
//
// This moved out of the (marketing) group so it picks up the authorized
// shell instead of the marketing TopNav/Footer — opening a case is an
// account action, and the design treats it as an authorized surface.

import { OpenCaseForm } from "@/components/product/open-case-form";
import { AuthShell } from "@/components/product/auth-shell";
import { Mono } from "@/components/mono";

export const dynamic = "force-dynamic";

export default async function NewCasePage({
  searchParams,
}: {
  searchParams: Promise<{ anchor_label?: string; first_message?: string }>;
}) {
  const params = await searchParams;
  const initialAnchorLabel = params.anchor_label ?? "";
  const initialMessage = params.first_message ?? "";

  return (
    <AuthShell>
      <div
        className="mx-auto max-w-[1060px] px-5 sm:px-8 py-12 sm:py-14"
        style={{ minHeight: "calc(100vh - 200px)" }}
      >
        {/* PageHead */}
        <header className="flex flex-col gap-3 sm:gap-4 pb-7 sm:pb-9 mb-10 sm:mb-11 border-b border-hair">
          <Mono muted size={10}>
            ACCOUNT · NEW CASE
          </Mono>
          <h1
            className="font-sans font-extrabold m-0 text-[40px] sm:text-[56px] leading-none"
            style={{ letterSpacing: "-0.04em" }}
          >
            Open a case.
          </h1>
          <p className="text-[15px] sm:text-[17px] text-text-muted m-0 max-w-[620px] leading-[1.5]">
            Anchor your inquiry to a property, then pick the question you want
            answered. Opening a case is free — you pay per answer. We&apos;ll
            reuse an existing case if you opened one for the same anchor in the
            last 30 days.
          </p>
        </header>

        <OpenCaseForm
          initialAnchorLabel={initialAnchorLabel}
          initialMessage={initialMessage}
        />
      </div>
    </AuthShell>
  );
}
