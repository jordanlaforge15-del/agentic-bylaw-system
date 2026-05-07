// Home page. Composes the sections defined in design_files/home.jsx in
// the same order: HeroSafe → HowItWorks → TryDemo → ProofGrid → ClosingCTA.
// We pick the "safe" hero variant as the default (per spec: bold reads as
// magazine cover, safe reads as product). The bold variant can be added
// later by toggling a query param if needed.

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
    <section className="px-8 pt-16 pb-14 mx-auto" style={{ maxWidth: 1340 }}>
      <div
        className="grid items-center"
        style={{ gridTemplateColumns: "1.05fr 1fr", gap: 56 }}
      >
        <div className="flex flex-col gap-[22px]">
          <Mono muted size={11}>
            HRM · PRIVATE BETA · MAY 2026
          </Mono>
          <h1
            className="font-sans font-extrabold m-0"
            style={{
              fontSize: 76,
              letterSpacing: "-0.045em",
              lineHeight: 0.95,
            }}
          >
            An expert
            <br />
            planner, in your
            <br />
            <HighlightWord>workflow.</HighlightWord>
          </h1>
          <p
            className="text-text-muted m-0"
            style={{ fontSize: 19, lineHeight: 1.4, maxWidth: 520 }}
          >
            ABS reads the Halifax Regional Municipality Land Use By-law,
            applied to your specific parcel. Ask in plain English. Get a
            sourced answer in seconds.
          </p>
          <div className="flex gap-2.5 mt-1.5">
            <Link href="/signup">
              <Btn variant="primary" size="lg">
                Get an invite →
              </Btn>
            </Link>
            <Link href="/pricing">
              <Btn variant="ghost" size="lg">
                See pricing
              </Btn>
            </Link>
          </div>
          <div className="flex gap-[18px] pt-3.5 mt-3 border-t border-hair">
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
      <div
        className="grid border border-hair"
        style={{ gridTemplateColumns: "repeat(3, 1fr)" }}
      >
        {steps.map((s, i) => (
          <div
            key={s.n}
            className="px-6 py-7 flex flex-col gap-3.5 relative"
            style={{ borderRight: i < 2 ? "1px solid var(--hair)" : "none" }}
          >
            <div className="flex items-center justify-between">
              <Mono muted size={11}>
                STEP · {s.n}
              </Mono>
              <span className="bg-accent" style={{ width: 24, height: 4 }} />
            </div>
            <div
              className="font-sans font-bold text-[28px] leading-[1.1]"
              style={{ letterSpacing: "-0.025em" }}
            >
              {s.t}
            </div>
            <div className="text-[14px] leading-[1.5] text-text-muted">
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
      <div
        className="grid items-center"
        style={{ gridTemplateColumns: "1fr 1fr", gap: 36 }}
      >
        <div className="flex flex-col gap-4">
          <h2
            className="font-sans font-bold text-[48px] leading-[1.05] m-0"
            style={{ letterSpacing: "-0.035em" }}
          >
            Paste an HRM address.
            <br />
            See what&apos;s permitted.
          </h2>
          <p
            className="text-[16px] text-text-muted leading-[1.5] m-0"
            style={{ maxWidth: 460 }}
          >
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
      <div
        className="grid"
        style={{ gridTemplateColumns: "repeat(3, 1fr)", gap: 14 }}
      >
        {PROOF.map((p, i) => (
          <div
            key={i}
            className="p-[22px] flex flex-col justify-between gap-2.5"
            style={{
              minHeight: 200,
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
                className="font-sans font-extrabold text-[24px] leading-[1.15]"
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
      <div
        className="grid items-center"
        style={{
          gridTemplateColumns: "1.4fr 1fr",
          gap: 32,
          background: "var(--text)",
          color: "var(--surface)",
          padding: "48px 36px",
        }}
      >
        <div>
          <h2
            className="font-sans font-extrabold m-0"
            style={{
              fontSize: 56,
              letterSpacing: "-0.045em",
              lineHeight: 0.95,
            }}
          >
            Maximize
            <br />
            your build.
          </h2>
          <p
            className="text-[16px] leading-[1.5] mt-4.5"
            style={{ color: "var(--text-muted)", maxWidth: 440 }}
          >
            Currently invite-only while we deepen HRM coverage. Tell us about
            your project and we&apos;ll get you in.
          </p>
        </div>
        <div className="flex flex-col gap-3">
          <Link href="/signup">
            <Btn variant="accent" size="lg">
              Request an invite →
            </Btn>
          </Link>
          <Link href="/pricing">
            <Btn
              variant="ghost"
              size="lg"
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
