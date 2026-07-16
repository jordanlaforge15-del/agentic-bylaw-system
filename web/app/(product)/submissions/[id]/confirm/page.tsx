// /submissions/[id]/confirm — human review of PDF-extracted attributes
// before the evaluator runs. Server component shell; the interactive
// table + confirm button live in the client island.

import { ConfirmClient } from "./confirm-client";
import { AdvisoryOnlyBanner } from "@/components/product/advisory-only-banner";
import { Mono } from "@/components/mono";

export const dynamic = "force-dynamic";

export default async function SubmissionConfirmPage({
  params,
}: {
  params: Promise<{ id: string }>;
}) {
  const { id } = await params;
  return (
    <div
      className="px-5 sm:px-8 py-10 sm:py-12 mx-auto max-w-[1080px]"
      style={{ minHeight: "calc(100vh - 280px)" }}
    >
      <header className="flex flex-col gap-3 pb-6 mb-6 border-b border-hair">
        <Mono muted size={11}>
          SUBMISSIONS · #{id} · CONFIRM
        </Mono>
        <h1
          className="font-sans font-extrabold m-0 text-[28px] sm:text-[36px] leading-[1]"
          style={{ letterSpacing: "-0.04em" }}
        >
          Review extracted attributes
        </h1>
        <p className="text-[14px] text-text-muted max-w-[640px]">
          The values below were extracted from a PDF document. Review
          each attribute, correct any errors, and confirm before
          running the compliance evaluator.
        </p>
      </header>

      <AdvisoryOnlyBanner />

      <ConfirmClient submissionId={Number(id)} />
    </div>
  );
}
