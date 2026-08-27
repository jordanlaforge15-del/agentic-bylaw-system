// /about — what ABS is, why it exists, and where it runs.
//
// Beta-era page. Deliberately small: no team grid, no funding timeline,
// no hiring CTA. ABS is a single-operator project in private beta and
// this page reflects that.

import type { Metadata } from "next";
import { pageMetadata } from "@/lib/page-metadata";
import Link from "next/link";
import { Mono } from "@/components/mono";
import { Btn } from "@/components/btn";
import { HighlightWord } from "@/components/highlight-word";
import { Page, PageHead } from "@/components/marketing/page-shell";

const PRINCIPLES = [
  {
    n: "01",
    t: "A reading must be sourced.",
    d: "Every assertion should carry a citation back to the exact paragraph in the consolidated bylaw. If we can't cite it, we'd rather refuse than guess.",
  },
  {
    n: "02",
    t: "Refuse before guessing.",
    d: "When two clauses conflict, ABS should surface both. When parcel data is uncertain, the answer should say so out loud.",
  },
  {
    n: "03",
    t: "Depth before breadth.",
    d: "One bylaw, indexed end-to-end and tested against real questions, before we add the next. The Coverage page lists exactly what's in scope today.",
  },
  {
    n: "04",
    t: "Not a replacement for HRM Planning.",
    d: "ABS is research, not a permit. Verify every reading with HRM Planning before relying on it for a permit application.",
  },
];

export const metadata: Metadata = pageMetadata({
  path: "/about",
  title: "About — ABS°",
  description:
    "Why ABS exists: a research tool that reads the Halifax Regional Centre Land Use By-law against one parcel and cites every assertion. Built in Halifax.",
});

export default function AboutPage() {
  return (
    <Page>
      <PageHead
        kicker="ABOUT · ABS"
        title="An expert planner, in your workflow."
        sub="ABS is a research tool that reads the Halifax Regional Centre Land Use By-law against a specific parcel and returns a sourced answer. It is in private beta."
      />

      {/* Mission */}
      <section className="py-10 sm:py-14 mb-6">
        <Mono muted size={11} className="mb-[18px] block">
          MISSION
        </Mono>
        <p
          className="m-0 font-sans max-w-[1080px] text-[32px] sm:text-[44px] lg:text-[56px]"
          style={{
            fontWeight: 700,
            letterSpacing: "-0.04em",
            lineHeight: 1.02,
          }}
        >
          Most good buildings get worse — or never get built — because the
          rules that govern them are{" "}
          <HighlightWord height={0.16}>too slow to read</HighlightWord>. We're
          working on the reading.
        </p>
      </section>

      {/* Principles */}
      <section className="mb-16 lg:mb-[72px]">
        <div className="flex items-center gap-3.5 mb-6">
          <Mono muted size={10}>
            PRINCIPLES · 04
          </Mono>
          <div className="flex-1 h-px bg-hair" />
        </div>
        <div className="grid grid-cols-1 sm:grid-cols-2 border border-hair">
          {PRINCIPLES.map((p, i) => (
            <div
              key={p.n}
              className={
                "flex flex-col gap-3.5 relative " +
                (i % 2 === 0 ? "sm:border-r border-hair " : "") +
                (i < 2 ? "border-b border-hair " : "") +
                (i === 0 ? "border-b sm:border-b border-hair" : "")
              }
              style={{ padding: "32px 30px" }}
            >
              <div className="flex items-start justify-between">
                <Mono accent size={11}>
                  § {p.n}
                </Mono>
                <span
                  className="bg-accent"
                  style={{ width: 28, height: 4 }}
                />
              </div>
              <div
                style={{
                  fontSize: 26,
                  fontWeight: 700,
                  letterSpacing: "-0.025em",
                  lineHeight: 1.1,
                }}
              >
                {p.t}
              </div>
              <div
                className="text-text-muted"
                style={{ fontSize: 14, lineHeight: 1.55 }}
              >
                {p.d}
              </div>
            </div>
          ))}
        </div>
      </section>

      {/* Where we are */}
      <section
        className="bg-text text-surface px-7 sm:px-9 py-10 mb-16 lg:mb-[72px] grid grid-cols-1 lg:grid-cols-2 gap-9 items-center"
      >
        <div className="flex flex-col gap-3.5">
          <Mono size={10} style={{ color: "rgba(255,255,255,0.55)" }}>
            WHERE WE ARE
          </Mono>
          <h3
            className="m-0 font-sans"
            style={{
              fontSize: 48,
              fontWeight: 800,
              letterSpacing: "-0.04em",
              lineHeight: 0.98,
            }}
          >
            Built in Halifax.
          </h3>
          <p
            className="m-0 max-w-[460px]"
            style={{
              fontSize: 15,
              lineHeight: 1.55,
              color: "rgba(255,255,255,0.7)",
            }}
          >
            ABS is an independent project. Halifax is where it started — small
            enough to learn end-to-end, and the city we walk past the
            buildings of every day.
          </p>
        </div>

        <div
          className="flex flex-col justify-center"
          style={{
            background: "rgba(255,255,255,0.04)",
            border: "1px solid rgba(255,255,255,0.18)",
            padding: 22,
            gap: 14,
          }}
        >
          <Mono size={9} style={{ color: "rgba(255,255,255,0.55)" }}>
            CURRENT COVERAGE
          </Mono>
          <div
            style={{
              fontSize: 18,
              fontWeight: 600,
              letterSpacing: "-0.015em",
              lineHeight: 1.3,
            }}
          >
            Halifax Regional Centre Land Use By-law
          </div>
          <div
            style={{
              fontSize: 13,
              lineHeight: 1.5,
              color: "rgba(255,255,255,0.7)",
            }}
          >
            The full document is in scope. More HRM bylaws are queued — see
            the{" "}
            <Link
              href="/coverage"
              className="underline"
              style={{ color: "rgba(255,255,255,0.9)" }}
            >
              Coverage page
            </Link>
            .
          </div>
        </div>
      </section>

      {/* Closing CTA */}
      <section className="mt-14 px-6 sm:px-8 py-7 border-[1.5px] border-text flex justify-between items-center gap-6 flex-wrap">
        <div className="flex flex-col gap-1">
          <Mono muted size={10}>
            SAY HELLO
          </Mono>
          <a
            href="mailto:info@agenticbylawsystems.com"
            className="no-underline text-text"
            style={{
              fontSize: 22,
              fontWeight: 700,
              letterSpacing: "-0.02em",
            }}
          >
            info@agenticbylawsystems.com
          </a>
        </div>
        <div className="flex gap-2.5">
          <Link href="/support">
            <Btn variant="ghost" size="md">
              Talk to us
            </Btn>
          </Link>
          <Link href="/signup">
            <Btn variant="primary" size="md">
              Get an invite →
            </Btn>
          </Link>
        </div>
      </section>
    </Page>
  );
}
