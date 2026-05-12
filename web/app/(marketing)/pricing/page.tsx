// /pricing — three tiers (Drafter / Practice / Developer), Practice
// flagged as recommended (inverted, accent "MOST POPULAR" tab). Below
// the tier grid: a 4-card FAQ block on surfaceAlt.

import Link from "next/link";
import { Btn } from "@/components/btn";
import { HighlightWord } from "@/components/highlight-word";
import { Mono } from "@/components/mono";

type Tier = {
  name: string;
  desc: string;
  price: string;
  cadence: string;
  features: string[];
  cta: string;
  ctaHref: string;
  featured?: boolean;
};

const TIERS: Tier[] = [
  {
    name: "Drafter",
    desc: "For homeowners and small projects.",
    price: "$24",
    cadence: "/ month",
    features: [
      "50 readings / month",
      "1 saved parcel",
      "Plain-language verdicts",
      "Sourced citations",
      "Email support",
    ],
    cta: "Start a project",
    ctaHref: "/signup",
  },
  {
    name: "Practice",
    desc: "For architects and design firms.",
    price: "$180",
    cadence: "/ seat / month",
    features: [
      "Unlimited readings",
      "Unlimited parcels",
      "Permit-ready exports",
      "Reading history & versioning",
      "Team workspace (up to 10 seats)",
      "Priority support",
    ],
    cta: "Get an invite",
    ctaHref: "/signup",
    featured: true,
  },
  {
    name: "Developer",
    desc: "For development teams and consultants.",
    price: "Custom",
    cadence: "",
    features: [
      "Everything in Practice",
      "API access",
      "Bulk parcel analysis",
      "Custom reporting",
      "SSO + audit logs",
      "Dedicated planner liaison",
    ],
    cta: "Talk to us",
    ctaHref: "mailto:hello@abs.app",
  },
];

const FAQS = [
  {
    q: "What counts as a reading?",
    a: "One question against one parcel. Follow-ups in the same conversation are free.",
  },
  {
    q: "Can I cancel anytime?",
    a: "Yes. Monthly plans cancel with one click. No call, no email.",
  },
  {
    q: "What jurisdictions are supported?",
    a: "Halifax Regional Municipality only, during private beta. We're adding Atlantic Canada cities through 2026.",
  },
  {
    q: "Is this legal advice?",
    a: "No. ABS° is research, not legal advice. Always verify with HRM Planning before submitting permits.",
  },
];

export default function PricingPage() {
  return (
    <div
      className="px-5 sm:px-8 py-10 sm:py-12 lg:py-14 mx-auto max-w-[1200px]"
      style={{ minHeight: "calc(100vh - 280px)" }}
    >
      <header className="flex flex-col gap-3 sm:gap-3.5 pb-6 sm:pb-7 mb-7 sm:mb-9 lg:mb-10 border-b border-hair">
        <Mono muted size={11}>
          PRICING · HRM PRIVATE BETA
        </Mono>
        <h1
          className="font-sans font-extrabold m-0 text-[36px] sm:text-[44px] lg:text-[56px] leading-[1] lg:leading-[0.98]"
          style={{ letterSpacing: "-0.04em" }}
        >
          Three tiers. <HighlightWord>One agent.</HighlightWord>
        </h1>
        <p className="text-[14px] sm:text-[16px] lg:text-[17px] text-text-muted leading-[1.45] m-0 max-w-[620px]">
          Beta pricing. Locks for the first year on any plan started before
          public launch. All prices in CAD.
        </p>
      </header>

      {/*
       * Tier grid. Mobile stacks the cards top-to-bottom so the
       * featured "Practice" card stays in the middle visually. At
       * `sm` we move to two columns (Drafter + Practice up top,
       * Developer beneath spanning both); at `lg` the original
       * three-across returns.
       */}
      <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-3 sm:gap-3.5">
        {TIERS.map((tier, i) => (
          <div
            key={tier.name}
            className={
              // Span both columns at sm if it's the third (Developer)
              // tier — keeps the visual hierarchy without orphaning a
              // single card on the bottom row.
              i === 2 ? "sm:col-span-2 lg:col-span-1" : ""
            }
          >
            <TierCard tier={tier} />
          </div>
        ))}
      </div>

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

function TierCard({ tier }: { tier: Tier }) {
  const featured = tier.featured;
  return (
    <div
      className="relative flex flex-col gap-5 sm:gap-[22px] p-6 sm:p-7 lg:p-7 min-h-[460px] sm:min-h-[500px] lg:min-h-[540px] h-full"
      style={{
        background: featured ? "var(--text)" : "var(--surface)",
        color: featured ? "var(--surface)" : "var(--text)",
        border: featured ? "none" : "1.5px solid var(--text)",
      }}
    >
      {featured && (
        <div
          className="absolute font-mono"
          style={{
            top: 0,
            right: 0,
            background: "var(--accent)",
            color: "var(--on-accent)",
            padding: "6px 11px",
            fontSize: 9.5,
            letterSpacing: "0.14em",
          }}
        >
          MOST POPULAR
        </div>
      )}

      <div className="flex flex-col gap-2.5">
        <Mono
          size={11}
          style={{
            color: featured
              ? "rgba(255,255,255,0.7)"
              : "var(--text-muted)",
          }}
        >
          TIER · {tier.name.toUpperCase()}
        </Mono>
        <div
          className="font-sans font-bold text-[26px] sm:text-[28px] lg:text-[32px] leading-[1]"
          style={{ letterSpacing: "-0.03em" }}
        >
          {tier.name}
        </div>
        <div
          className="text-[13.5px] leading-[1.4]"
          style={{
            color: featured
              ? "rgba(255,255,255,0.65)"
              : "var(--text-muted)",
          }}
        >
          {tier.desc}
        </div>
      </div>

      <div
        className="flex items-baseline gap-1.5 pb-[18px]"
        style={{
          borderBottom: featured
            ? "1px solid rgba(255,255,255,0.15)"
            : "1px solid var(--hair)",
        }}
      >
        <span
          className="font-sans font-extrabold text-[44px] sm:text-[48px] lg:text-[56px] leading-[1]"
          style={{ letterSpacing: "-0.04em" }}
        >
          {tier.price}
        </span>
        {tier.cadence && (
          <span
            className="text-[14px]"
            style={{
              color: featured
                ? "rgba(255,255,255,0.6)"
                : "var(--text-muted)",
            }}
          >
            {tier.cadence}
          </span>
        )}
      </div>

      <ul className="list-none p-0 m-0 flex flex-col gap-2.5 flex-1">
        {tier.features.map((f) => (
          <li
            key={f}
            className="flex items-start gap-2.5 text-[13.5px] leading-[1.45]"
          >
            <span
              className="font-mono"
              style={{
                color: featured ? "var(--accent)" : "var(--accent-ink)",
                fontSize: 11,
                paddingTop: 1,
              }}
            >
              +
            </span>
            <span>{f}</span>
          </li>
        ))}
      </ul>

      <Link href={tier.ctaHref} className="contents">
        <Btn
          variant={featured ? "accent" : "primary"}
          size="md"
          className="w-full"
        >
          {tier.cta} →
        </Btn>
      </Link>
    </div>
  );
}
