// /cases/new — the free, conversation-first entry point (ABS-385 · beta
// pivot I6). Rendered inside the authorized shell (AuthBar + AuthFooter),
// not the marketing chrome. Reached from the /app sidebar's "+ Open a case"
// action and the workspace menu.
//
// Starting a conversation is FREE — the user anchors to a property, asks
// their question, and the case is opened with that question already sent.
// Released report SKUs are a secondary accordion. All the interaction lives
// in the OpenCaseForm client component.

import { OpenCaseForm } from "@/components/product/open-case-form";
import { AuthShell } from "@/components/product/auth-shell";
import { Mono } from "@/components/mono";

export const dynamic = "force-dynamic";

export default async function NewCasePage({
  searchParams,
}: {
  searchParams: Promise<{
    anchor_label?: string;
    first_message?: string;
    report?: string;
  }>;
}) {
  const params = await searchParams;
  const initialAnchorLabel = params.anchor_label ?? "";
  const initialMessage = params.first_message ?? "";
  const initialReportSlug = params.report ?? "";

  return (
    <AuthShell>
      <div
        className="mx-auto max-w-[1060px] px-5 sm:px-8 py-12 sm:py-14"
        style={{ minHeight: "calc(100vh - 200px)" }}
      >
        {/* PageHead */}
        <header className="flex flex-col gap-3 sm:gap-4 pb-7 sm:pb-9 mb-10 sm:mb-11 border-b border-hair">
          <Mono muted size={10}>
            ACCOUNT · NEW CONVERSATION
          </Mono>
          <h1
            className="font-sans font-extrabold m-0 text-[40px] sm:text-[56px] leading-none"
            style={{ letterSpacing: "-0.04em" }}
          >
            Start a conversation.
          </h1>
          <p className="text-[15px] sm:text-[17px] text-text-muted m-0 max-w-[620px] leading-[1.5]">
            Anchor to a property, ask your question, and we&apos;ll open the
            case with your question already sent. Starting a conversation is
            free — you only use turns once the assistant replies.
          </p>
        </header>

        <OpenCaseForm
          initialAnchorLabel={initialAnchorLabel}
          initialMessage={initialMessage}
          initialReportSlug={initialReportSlug}
        />
      </div>
    </AuthShell>
  );
}
