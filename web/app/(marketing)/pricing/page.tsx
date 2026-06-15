// /pricing — priced-question menu.
//
// Renders the five-question launch catalog (data-driven from the
// backend at /v1/billing/questions) so prices stay in lockstep with
// the catalog code and the decision doc. An "Other" card links to the
// off-menu path for questions outside the fixed menu.

import { ADVISOR_API_URL } from "@/lib/api";
import {
  QuestionMenuResponse,
  QuestionMenuItem,
  formatCurrency,
} from "@/lib/cases";
import { HighlightWord } from "@/components/highlight-word";
import { Mono } from "@/components/mono";
import { BuyQuestionButton } from "@/components/marketing/buy-question-button";
import Link from "next/link";

export const dynamic = "force-dynamic";

const FAQS = [
  {
    q: "What do I get?",
    a: "A sourced answer to the exact question you selected — grounded in the current Halifax Regional Centre Land Use By-law, with citations to the governing provisions. Delivered in writing, ready to attach to a permit application or due-diligence report.",
  },
  {
    q: "What if my question isn't on the list?",
    a: 'Use the "Other" option to describe your situation. We\'ll review whether it\'s groundable in the by-law and quote the research separately.',
  },
  {
    q: "What if the question can't be answered?",
    a: "If we can't ground a sourced answer in the available by-law data — for instance the parcel is outside our coverage area — you won't be charged.",
  },
  {
    q: "What jurisdictions are supported?",
    a: "Halifax Regional Centre (the urban core of HRM, governed by the Regional Centre Land Use By-law) during private beta. More HRM bylaws are coming through 2026.",
  },
];


async function fetchQuestionMenu(): Promise<QuestionMenuResponse | null> {
  // Server-side fetch — hit the backend directly rather than going
  // through our own /api proxy, since this is already a server component.
  try {
    const r = await fetch(`${ADVISOR_API_URL}/v1/billing/questions`, {
      cache: "no-store",
    });
    if (!r.ok) return null;
    return (await r.json()) as QuestionMenuResponse;
  } catch {
    return null;
  }
}


export default async function PricingPage() {
  const menu = await fetchQuestionMenu();
  return (
    <div
      className="px-5 sm:px-8 py-10 sm:py-12 lg:py-14 mx-auto max-w-[1200px]"
      style={{ minHeight: "calc(100vh - 280px)" }}
    >
      <header className="flex flex-col gap-3 sm:gap-3.5 pb-6 sm:pb-7 mb-7 sm:mb-9 lg:mb-10 border-b border-hair">
        <Mono muted size={11}>
          PRICING · QUESTIONS
        </Mono>
        <h1
          className="font-sans font-extrabold m-0 text-[36px] sm:text-[44px] lg:text-[56px] leading-[1] lg:leading-[0.98]"
          style={{ letterSpacing: "-0.04em" }}
        >
          Pick your question.{" "}
          <HighlightWord>Get a sourced answer.</HighlightWord>
        </h1>
        <p className="text-[14px] sm:text-[16px] lg:text-[17px] text-text-muted leading-[1.45] m-0 max-w-[680px]">
          Select the question that matches your situation. We read the
          by-law, locate the governing provisions, and deliver a written
          answer with citations — ready to attach to a permit application
          or due-diligence file.
        </p>
      </header>

      {menu === null ? (
        <MenuUnavailable />
      ) : (
        <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-3 sm:gap-3.5">
          {menu.questions.map((q) => (
            <QuestionCard
              key={q.slug}
              question={q}
              currency={menu.currency}
            />
          ))}
          <OtherCard />
        </div>
      )}

      <div className="mt-9 sm:mt-12 lg:mt-14 grid grid-cols-1 sm:grid-cols-2 gap-3 sm:gap-4 lg:gap-[18px]">
        {FAQS.map((f) => (
          <div
            key={f.q}
            className="bg-surface-alt border border-hair p-5 sm:p-[20px_22px]"
          >
            <div
              className="text-[14px] sm:text-[15px] font-semibold mb-1.5"
              style={{ letterSpacing: "-0.01em" }}
            >
              {f.q}
            </div>
            <div className="text-[13px] sm:text-[13.5px] text-text-muted leading-[1.5]">
              {f.a}
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}


function QuestionCard({
  question,
  currency,
}: {
  question: QuestionMenuItem;
  currency: string;
}) {
  return (
    <div
      className="bg-surface border border-hair p-5 flex flex-col gap-3 min-h-[220px]"
      data-testid={`question-card-${question.slug}`}
    >
      <div>
        <div
          className="text-[32px] font-extrabold leading-none mb-0.5"
          style={{ letterSpacing: "-0.03em" }}
        >
          {formatCurrency(question.price_cents, question.currency || currency)}
        </div>
        <div className="text-[12px] text-text-muted">CAD · per answer</div>
      </div>
      <div className="flex-1">
        <div
          className="font-semibold text-[14px] sm:text-[15px] mb-1"
          style={{ letterSpacing: "-0.01em" }}
        >
          {question.display_name}
        </div>
        <div className="text-[13px] text-text-muted leading-[1.45]">
          {question.summary}
        </div>
      </div>
      <div className="pt-1">
        <BuyQuestionButton
          questionSlug={question.slug}
          available={question.available}
        />
      </div>
    </div>
  );
}


function OtherCard() {
  return (
    <div className="bg-surface-alt border border-hair p-5 flex flex-col gap-3 min-h-[220px]">
      <div>
        <div
          className="font-semibold text-[14px] sm:text-[15px] mb-1"
          style={{ letterSpacing: "-0.01em" }}
        >
          My question isn&apos;t on the list
        </div>
        <div className="text-[13px] text-text-muted leading-[1.45]">
          Describe your situation and we&apos;ll review whether it&apos;s
          groundable in the by-law and quote the research separately.
        </div>
      </div>
      <div className="mt-auto pt-1">
        <Link href="/support">
          <button className="w-full border border-hair px-3 py-2 text-[12.5px] font-semibold text-center hover:bg-surface transition-colors tracking-[-0.01em]">
            Get in touch →
          </button>
        </Link>
      </div>
    </div>
  );
}


function MenuUnavailable() {
  return (
    <div className="bg-surface-alt border border-hair p-8 text-center">
      <div className="font-semibold mb-2">Pricing temporarily unavailable</div>
      <div className="text-text-muted text-[13.5px]">
        We couldn&apos;t load the question menu. Refresh in a moment, or
        contact us at{" "}
        <a className="underline" href="mailto:info@agenticbylawsystems.com">
          info@agenticbylawsystems.com
        </a>{" "}
        for direct pricing.
      </div>
    </div>
  );
}
