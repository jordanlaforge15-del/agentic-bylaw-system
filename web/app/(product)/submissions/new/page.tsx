// /submissions/new — upload an IFC file, pick a parcel, ingest, redirect.

import { SubmissionUploadForm } from "./upload-form";
import { AdvisoryOnlyBanner } from "@/components/product/advisory-only-banner";
import { Mono } from "@/components/mono";

export const dynamic = "force-dynamic";

export default function NewSubmissionPage() {
  return (
    <div
      className="px-5 sm:px-8 py-10 sm:py-12 mx-auto max-w-[820px]"
      style={{ minHeight: "calc(100vh - 280px)" }}
    >
      <header className="flex flex-col gap-3 pb-6 mb-6 border-b border-hair">
        <Mono muted size={11}>
          SUBMISSIONS · NEW
        </Mono>
        <h1
          className="font-sans font-extrabold m-0 text-[28px] sm:text-[36px] leading-[1]"
          style={{ letterSpacing: "-0.04em" }}
        >
          Upload a BIM model
        </h1>
        <p className="text-[14px] text-text-muted m-0 max-w-[600px]">
          Drop an <code>.ifc</code> file from your modelling tool. We extract
          building attributes (height, GFA, footprint, parking, …),
          compute setbacks against the parcel polygon, and show you a
          compliance matrix.
        </p>
      </header>

      <AdvisoryOnlyBanner />

      <SubmissionUploadForm />
    </div>
  );
}
