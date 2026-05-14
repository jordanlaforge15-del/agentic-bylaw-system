// /pricing — case-credit pack matrix.
//
// Three tiers (Quick / Standard / Complex) × four pack SKUs (PAYG /
// Starter / Pro / Enterprise) = 12 buy options. The catalog is fetched
// from the backend so prices stay in lockstep with the Stripe-side
// Price IDs (the backend's PackOffer.amount_due_cents is the source
// of truth).
//
// "Standard" is the recommended middle tier — flagged with the same
// inverted card treatment the v1 design used for "Practice".

import { ADVISOR_API_URL } from "@/lib/api";
import {
  CatalogResponse,
  Tier,
  TIER_DISPLAY,
  formatCurrency,
  formatDiscount,
  formatTokenBudget,
} from "@/lib/cases";
import { HighlightWord } from "@/components/highlight-word";
import { Mono } from "@/components/mono";
import { BuyPackButton } from "@/components/marketing/buy-pack-button";

export const dynamic = "force-dynamic";

const TIER_BLURBS: Record<Tier, string> = {
  quick:
    "Single-property zoning lookups, permitted-use checks. ~4–6 retrieval rounds.",
  standard:
    "Variance research, multi-bylaw cross-references, development standards. ~12–18 retrieval rounds.",
  complex:
    "Rezoning, multi-overlay analysis, deep development-application files. ~35–50 retrieval rounds.",
};

const TIER_ORDER: Tier[] = ["quick", "standard", "complex"];

const FAQS = [
  {
    q: "What counts as a case?",
    a: "A bylaw research inquiry tied to one specific property address, project reference, or development application. Follow-up questions in the same case don't cost extra.",
  },
  {
    q: "Do unused credits expire?",
    a: "Pay-as-you-go and starter credits never expire. Pro and enterprise packs may carry a renewal window; check your invoice.",
  },
  {
    q: "Can I upgrade a case mid-research?",
    a: "Yes. If a case outgrows its tier the agent will surface an upgrade prompt; one click swaps the credit for a higher tier and the budget extends accordingly.",
  },
  {
    q: "What jurisdictions are supported?",
    a: "Halifax Regional Municipality only, during private beta. We're adding Atlantic Canada cities through 2026.",
  },
];


async function fetchCatalog(): Promise<CatalogResponse | null> {
  // Server-side fetch — we hit the backend directly rather than going
  // through our own /api proxy because this is a server component
  // already on the server. Skips a round-trip.
  try {
    const r = await fetch(`${ADVISOR_API_URL}/v1/billing/catalog`, {
      cache: "no-store",
    });
    if (!r.ok) return null;
    return (await r.json()) as CatalogResponse;
  } catch {
    return null;
  }
}


export default async function PricingPage() {
  const catalog = await fetchCatalog();
  return (
    <div
      className="px-5 sm:px-8 py-10 sm:py-12 lg:py-14 mx-auto max-w-[1200px]"
      style={{ minHeight: "calc(100vh - 280px)" }}
    >
      <header className="flex flex-col gap-3 sm:gap-3.5 pb-6 sm:pb-7 mb-7 sm:mb-9 lg:mb-10 border-b border-hair">
        <Mono muted size={11}>
          PRICING · CASE CREDITS
        </Mono>
        <h1
          className="font-sans font-extrabold m-0 text-[36px] sm:text-[44px] lg:text-[56px] leading-[1] lg:leading-[0.98]"
          style={{ letterSpacing: "-0.04em" }}
        >
          One credit. <HighlightWord>One case.</HighlightWord>
        </h1>
        <p className="text-[14px] sm:text-[16px] lg:text-[17px] text-text-muted leading-[1.45] m-0 max-w-[680px]">
          Pay per file, not per seat. Bill the credit to your client as a
          disbursement on their invoice. Pick the tier that matches the
          depth of the inquiry — the agent will tell you mid-research if
          you've undersized.
        </p>
      </header>

      {catalog === null ? (
        <CatalogUnavailable />
      ) : (
        <div className="flex flex-col gap-10 sm:gap-12 lg:gap-14">
          {TIER_ORDER.map((tierName) => (
            <TierSection
              key={tierName}
              tierName={tierName}
              catalog={catalog}
            />
          ))}
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


function CatalogUnavailable() {
  return (
    <div className="bg-surface-alt border border-hair p-8 text-center">
      <div className="font-semibold mb-2">Pricing temporarily unavailable</div>
      <div className="text-text-muted text-[13.5px]">
        We couldn&apos;t load the live catalog. Refresh in a moment, or
        contact us at{" "}
        <a className="underline" href="mailto:hello@abs.app">
          hello@abs.app
        </a>{" "}
        for direct pricing.
      </div>
    </div>
  );
}


function TierSection({
  tierName,
  catalog,
}: {
  tierName: Tier;
  catalog: CatalogResponse;
}) {
  const offers = catalog.offers.filter((o) => o.tier === tierName);
  if (offers.length === 0) return null;
  const sample = offers[0];
  const featured = tierName === "standard";
  return (
    <section>
      <div className="mb-4 sm:mb-5 flex flex-col gap-1.5">
        <div className="flex items-baseline justify-between gap-3 flex-wrap">
          <h2
            className="font-sans font-extrabold m-0 text-[24px] sm:text-[28px] lg:text-[32px]"
            style={{ letterSpacing: "-0.03em" }}
          >
            {sample.tier_display_name}
            {featured && (
              <span
                className="ml-3 align-middle inline-block px-2 py-1 bg-accent text-on-accent text-[10px] font-mono uppercase"
                style={{ letterSpacing: "0.06em" }}
              >
                Most popular
              </span>
            )}
          </h2>
          <Mono muted size={12}>
            {formatTokenBudget(sample.tier_token_budget)} per case
          </Mono>
        </div>
        <p className="text-[13.5px] sm:text-[14px] text-text-muted m-0 max-w-[680px]">
          {TIER_BLURBS[tierName]}
        </p>
      </div>

      <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-3 sm:gap-3.5">
        {offers.map((offer) => (
          <PackCard
            key={`${offer.tier}-${offer.pack_sku}`}
            offer={offer}
            currency={catalog.currency}
            highlightTier={featured}
          />
        ))}
      </div>
    </section>
  );
}


function PackCard({
  offer,
  currency,
  highlightTier,
}: {
  offer: CatalogResponse["offers"][number];
  currency: string;
  highlightTier: boolean;
}) {
  const featured = highlightTier && offer.pack_sku === "starter";
  const tone = featured
    ? "bg-text text-surface border-text"
    : "bg-surface text-text border-hair";
  return (
    <div className={`${tone} border p-5 flex flex-col gap-3 min-h-[200px]`}>
      <div className="flex items-baseline justify-between">
        <Mono size={11} muted={!featured}>
          {offer.pack_display_name.toUpperCase()}
        </Mono>
        {offer.discount_bps > 0 && (
          <span className="text-[11px] font-mono uppercase">
            {formatDiscount(offer.discount_bps)}
          </span>
        )}
      </div>
      <div className="flex flex-col gap-0.5">
        <div className="text-[28px] font-extrabold leading-none" style={{ letterSpacing: "-0.03em" }}>
          {formatCurrency(offer.amount_due_cents, offer.currency || currency)}
        </div>
        <div className={`text-[12.5px] ${featured ? "opacity-70" : "text-text-muted"}`}>
          {offer.quantity === 1
            ? `1 ${offer.tier} credit`
            : `${offer.quantity} × ${offer.tier} credits`}
        </div>
        {offer.discount_bps > 0 && (
          <div className={`text-[11.5px] ${featured ? "opacity-50 line-through" : "text-text-muted line-through"}`}>
            {formatCurrency(offer.list_price_cents, offer.currency || currency)}{" "}
            list
          </div>
        )}
      </div>
      <div className="mt-auto pt-2">
        <BuyPackButton
          tier={offer.tier}
          packSku={offer.pack_sku}
          available={offer.available}
          featured={featured}
        />
      </div>
    </div>
  );
}
