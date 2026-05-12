// Home page. Composes the sections defined in design_files/home.jsx in
// the same order: HeroSafe → HowItWorks → TryDemo → ProofGrid → ClosingCTA.
// We pick the "safe" hero variant as the default (per spec: bold reads as
// magazine cover, safe reads as product). The bold variant can be added
// later by toggling a query param if needed.
//
// Responsive contract (per design_handoff_abs_website/responsive):
//   - base (< 640): single column. Hero stacks copy → CTA → AgentReader.
//     HowItWorks stacks vertically. TryDemo stacks. ProofGrid is one
//     column. ClosingCTA stacks copy then buttons.
//   - sm (≥ 640): two columns where it makes sense (TryDemo, ProofGrid).
//     Hero stays stacked but the AgentReader gets full width below the
//     copy.
//   - lg (≥ 1024): the original desktop layout — hero side-by-side,
//     three-column HowItWorks and ProofGrid, asymmetric ClosingCTA.

import Link from "next/link";
import { AgentWalkthrough } from "@/components/home/agent-walkthrough";
import { AddressDemo } from "@/components/home/address-demo";
import { Btn } from "@/components/btn";
import { HighlightWord } from "@/components/highlight-word";
import { Mono } from "@/components/mono";
import { Section } from "@/components/section";
import { Stat } from "@/components/stat";
import { PROOF } from "@/lib/mock";

export default function HomePage() {
  return (
    <>
      <HeroSafe />
      <HowItWorks />
      <TryDemo />
      <ProofGrid />
      <ClosingCTA />
    </>
  );
}

function HeroSafe() {
  return (
    <section className="px-5 sm:px-8 pt-10 sm:pt-14 lg:pt-16 pb-10 sm:pb-12 lg:pb-14 mx-auto max-w-[1340px]">
      {/*
       * Hero grid. Below `lg` we stack copy first (full width), then
       * the AgentReader sits underneath. At `lg`+ the asymmetric
       * 1.05fr / 1fr split returns.
       */}
      <div className="grid items-center gap-10 lg:gap-14 lg:[grid-template-columns:1.05fr_1fr]">
        <div className="flex flex-col gap-5 sm:gap-[22px]">
          <Mono muted size={11}>
            HRM · PRIVATE BETA · MAY 2026
          </Mono>
          <h1
            className="font-sans font-extrabold m-0 text-[42px] sm:text-[64px] lg:text-[76px] leading-[0.96] sm:leading-[0.95]"
            style={{ letterSpacing: "-0.045em" }}
          >
            An expert
            <br />
            planner, in your
            <br />
            <HighlightWord>workflow.</HighlightWord>
          </h1>
          <p className="text-text-muted m-0 text-[15px] sm:text-[17px] lg:text-[19px] leading-[1.45] lg:leading-[1.4] max-w-[520px]">
            ABS reads the Halifax Regional Municipality Land Use By-law,
            applied to your specific parcel. Ask in plain English. Get a
            sourced answer in seconds.
          </p>
          <div className="flex flex-col sm:flex-row gap-2 sm:gap-2.5 mt-1 sm:mt-1.5">
            <Link href="/signup" className="contents">
              <Btn variant="primary" size="lg" className="w-full sm:w-auto">
                Get an invite →
              </Btn>
            </Link>
            <Link href="/pricing" className="contents">
              <Btn variant="ghost" size="lg" className="w-full sm:w-auto">
                See pricing
              </Btn>
            </Link>
          </div>
          <div className="flex gap-4 sm:gap-[18px] pt-3.5 mt-3 border-t border-hair">
            <Stat n="HRM" l="JURISDICTION" />
            <Stat n="38k" l="PARCELS INDEXED" />
            <Stat n="0.94" l="AVG. CONFIDENCE" />
          </div>
        </div>
        <AgentWalkthrough />
      </div>
    </section>
  );
}

function HowItWorks() {
  const steps = [
    {
      n: "01",
      t: "Ask",
      d: 'Plain English. "Can I add a backyard suite?" "How tall can I build?" Type it like you would to a planner.',
    },
    {
      n: "02",
      t: "ABS reads",
      d: "The agent locates your parcel, opens the relevant sections of the HRM Land Use By-law, and works the math.",
    },
    {
      n: "03",
      t: "You get a sourced answer",
      d: "A verdict, the reasoning, and citations to the exact sections — ready to attach to a permit application.",
    },
  ];
  return (
    <Section
      kicker="HOW IT WORKS · 3 STEPS"
      title="The bylaw, read for you. Sourced and dated."
    >
      {/*
       * Steps grid. On mobile each step is its own row separated by a
       * hairline border-bottom; at `lg` they snap into the three-column
       * row defined by the design.
       */}
      <div className="grid grid-cols-1 lg:grid-cols-3 border border-hair">
        {steps.map((s, i) => (
          <div
            key={s.n}
            className="px-5 sm:px-6 py-6 sm:py-7 flex flex-col gap-3 sm:gap-3.5 relative
              border-b border-hair last:border-b-0
              lg:border-b-0 lg:border-r lg:last:border-r-0"
          >
            <div className="flex items-center justify-between">
              <Mono muted size={11}>
                STEP · {s.n}
              </Mono>
              <span className="bg-accent" style={{ width: 24, height: 4 }} />
            </div>
            <div
              className="font-sans font-bold text-[22px] sm:text-[24px] lg:text-[28px] leading-[1.1]"
              style={{ letterSpacing: "-0.025em" }}
            >
              {s.t}
            </div>
            <div className="text-[13px] sm:text-[14px] leading-[1.5] text-text-muted">
              {s.d}
            </div>
          </div>
        ))}
      </div>
    </Section>
  );
}

function TryDemo() {
  return (
    <Section kicker="TRY IT · NO ACCOUNT NEEDED">
      <div className="grid items-center gap-7 lg:gap-9 grid-cols-1 lg:grid-cols-2">
        <div className="flex flex-col gap-3 sm:gap-4">
          <h2
            className="font-sans font-bold text-[28px] sm:text-[36px] lg:text-[48px] leading-[1.05] m-0"
            style={{ letterSpacing: "-0.035em" }}
          >
            Paste an HRM address.
            <br />
            See what&apos;s permitted.
          </h2>
          <p className="text-[14px] sm:text-[15px] lg:text-[16px] text-text-muted leading-[1.5] m-0 max-w-[460px]">
            The full agent runs the same way once you&apos;re in. This is a
            slice — one question, one reading, one source.
          </p>
        </div>
        <AddressDemo />
      </div>
    </Section>
  );
}

function ProofGrid() {
  return (
    <Section
      kicker="REAL READINGS · ANONYMIZED"
      title="What ABS has answered this week."
    >
      {/*
       * 1 col mobile → 2 col tablet → 3 col desktop. The "featured"
       * (accent) card stays accent-coloured at every breakpoint; it
       * just fits the local grid rather than spanning anything special.
       */}
      <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-3 sm:gap-3.5">
        {PROOF.map((p, i) => (
          <div
            key={i}
            className="p-4 sm:p-5 lg:p-[22px] flex flex-col justify-between gap-2.5 min-h-[180px] lg:min-h-[200px]"
            style={{
              background: p.accent ? "var(--accent)" : "var(--surface-alt)",
              color: p.accent ? "var(--on-accent)" : "var(--text)",
              border: p.accent ? "none" : "1px solid var(--hair)",
            }}
          >
            <Mono
              size={9.5}
              style={{
                color: p.accent ? "var(--on-accent)" : "var(--text-muted)",
              }}
            >
              {p.addr}
            </Mono>
            <div>
              <div
                className="text-[13px] italic mb-1.5"
                style={{
                  color: p.accent ? "var(--on-accent)" : "var(--text-muted)",
                }}
              >
                &ldquo;{p.q}&rdquo;
              </div>
              <div
                className="font-sans font-extrabold text-[20px] sm:text-[22px] lg:text-[24px] leading-[1.15]"
                style={{ letterSpacing: "-0.03em" }}
              >
                {p.a}
              </div>
            </div>
            <Mono
              size={9.5}
              style={{
                color: p.accent ? "var(--on-accent)" : "var(--text-muted)",
              }}
            >
              {p.cite}
            </Mono>
          </div>
        ))}
      </div>
    </Section>
  );
}

function ClosingCTA() {
  return (
    <Section kicker="JOIN THE BETA">
      {/*
       * Mobile: copy on top, buttons full-width below. Tablet+: the
       * asymmetric 1.4 / 1 grid from the desktop spec, with the copy
       * column on the left and CTAs stacked on the right.
       */}
      <div
        className="grid items-center gap-6 lg:gap-8 grid-cols-1 lg:[grid-template-columns:1.4fr_1fr] p-7 sm:p-9 lg:px-9 lg:py-12"
        style={{
          background: "var(--text)",
          color: "var(--surface)",
        }}
      >
        <div>
          <h2
            className="font-sans font-extrabold m-0 text-[34px] sm:text-[44px] lg:text-[56px] leading-[0.98] sm:leading-[0.95]"
            style={{ letterSpacing: "-0.045em" }}
          >
            Maximize
            <br />
            your build.
          </h2>
          <p
            className="text-[14px] sm:text-[16px] leading-[1.5] mt-3 sm:mt-4 max-w-[440px]"
            style={{ color: "var(--text-muted)" }}
          >
            Currently invite-only while we deepen HRM coverage. Tell us about
            your project and we&apos;ll get you in.
          </p>
        </div>
        <div className="flex flex-col gap-2.5 sm:gap-3">
          <Link href="/signup" className="contents">
            <Btn variant="accent" size="lg" className="w-full">
              Request an invite →
            </Btn>
          </Link>
          <Link href="/pricing" className="contents">
            <Btn
              variant="ghost"
              size="lg"
              className="w-full"
              style={{
                borderColor: "var(--surface)",
                color: "var(--surface)",
              }}
            >
              See pricing
            </Btn>
          </Link>
        </div>
      </div>
    </Section>
  );
}
