// /submissions/[id] — review extracted attributes, override, evaluate,
// display the compliance matrix. Server component shell + a client
// island for the interactive bits (override + evaluate button).

import { SubmissionDetailClient } from "./detail-client";
import { AdvisoryOnlyBanner } from "@/components/product/advisory-only-banner";
import { Mono } from "@/components/mono";

export const dynamic = "force-dynamic";

export default async function SubmissionDetailPage({
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
          SUBMISSIONS · #{id}
        </Mono>
        <h1
          className="font-sans font-extrabold m-0 text-[28px] sm:text-[36px] leading-[1]"
          style={{ letterSpacing: "-0.04em" }}
        >
          Submission review
        </h1>
      </header>

      <AdvisoryOnlyBanner />

      <SubmissionDetailClient submissionId={Number(id)} />
    </div>
  );
}
