// /app/answers/[id] — post-purchase answer delivery surface (ABS-321).
//
// Where the user lands after they buy (or, payments-off, spend a free
// credit on) a priced question. Resolves the purchase by id, runs the
// answer if it hasn't run yet, renders the grounded output with the
// ABS-313 disclaimer, and offers the bounded refinement window
// (ABS-312/317).
//
// One surface, two entrypoints: today it is reached INTERNALLY from the
// case-open / free-question flow (ABS-320/322); when
// ADVISOR_PAYMENTS_ENABLED=true it additionally becomes the Stripe
// Checkout success_url target. Both arrive here with a purchase id.
//
// Server component owns only the app chrome (same "back to chat" header
// as /app/billing); all the interactive fetch/answer/refine logic lives
// in the AnswerView client island. Route sits under /app(.*) so the
// middleware auth gate protects it.

import Link from "next/link";
import { AnswerView } from "@/components/product/answer-view";
import { Mono } from "@/components/mono";

export const dynamic = "force-dynamic";

export default async function AnswerPage({
  params,
}: {
  params: Promise<{ id: string }>;
}) {
  const { id } = await params;
  const purchaseId = Number(id);

  return (
    <div className="flex flex-col h-dvh bg-surface text-text overflow-hidden">
      {/* App header — mirrors /app/billing so the chrome stays consistent. */}
      <div className="border-b border-hair bg-surface flex items-center justify-between px-3 sm:px-5 py-2.5 sm:py-3 safe-pt safe-px gap-2">
        <div className="flex items-center gap-2.5 sm:gap-3.5 min-w-0">
          <Link
            href="/app"
            className="flex items-center gap-2 text-text-muted hover:text-text transition-colors"
          >
            <svg
              width="16"
              height="16"
              viewBox="0 0 16 16"
              fill="none"
              stroke="currentColor"
              strokeWidth="2"
            >
              <path d="M13 8H3m0 0l6-6m-6 6l6 6" />
            </svg>
            <span className="text-sm">Back to chat</span>
          </Link>
        </div>
        <Mono muted className="hidden lg:inline">
          ANSWER · #{id}
        </Mono>
      </div>

      {/* Main content */}
      <div className="flex-1 overflow-y-auto">
        <div className="px-5 sm:px-8 py-10 sm:py-12 lg:py-14 mx-auto max-w-[820px]">
          {Number.isFinite(purchaseId) && purchaseId > 0 ? (
            <AnswerView purchaseId={purchaseId} />
          ) : (
            <div className="bg-surface-alt border border-hair p-8">
              <div className="font-semibold mb-2">Invalid answer link</div>
              <div className="text-text-muted text-[13.5px]">
                That purchase id isn&apos;t valid. Head back to{" "}
                <Link href="/cases/new" className="underline text-text">
                  the question menu
                </Link>{" "}
                to buy an answer.
              </div>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
