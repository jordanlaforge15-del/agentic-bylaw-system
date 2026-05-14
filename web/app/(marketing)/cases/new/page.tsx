// /cases/new — opens a new case (or reopens an in-window match) and
// redirects to the chat product with the case_id pre-bound. Uses the
// CaseOpenForm client component for the interactive bits.

import Link from "next/link";
import { CaseOpenForm } from "@/components/marketing/case-open-form";
import { Mono } from "@/components/mono";

export const dynamic = "force-dynamic";

export default function NewCasePage() {
  return (
    <div
      className="px-5 sm:px-8 py-10 sm:py-12 lg:py-14 mx-auto max-w-[820px]"
      style={{ minHeight: "calc(100vh - 280px)" }}
    >
      <header className="flex flex-col gap-3 sm:gap-3.5 pb-6 sm:pb-7 mb-7 sm:mb-9 border-b border-hair">
        <Mono muted size={11}>
          ACCOUNT · NEW CASE
        </Mono>
        <h1
          className="font-sans font-extrabold m-0 text-[28px] sm:text-[36px] lg:text-[42px] leading-[1]"
          style={{ letterSpacing: "-0.04em" }}
        >
          Open a case
        </h1>
        <p className="text-[14px] text-text-muted m-0 max-w-[600px]">
          Anchor your inquiry to a specific property, project ref, or
          development application. We&apos;ll classify the question to
          recommend a tier, and reuse an existing case if you opened one
          for the same anchor in the last 30 days.
        </p>
      </header>

      <CaseOpenForm />

      <div className="mt-8 text-[12.5px] text-text-muted">
        Need credits first?{" "}
        <Link href="/pricing" className="underline">
          See pricing
        </Link>{" "}
        ·{" "}
        <Link href="/cases" className="underline">
          Back to my cases
        </Link>
      </div>
    </div>
  );
}
