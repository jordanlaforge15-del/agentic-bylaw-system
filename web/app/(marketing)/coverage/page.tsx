// /coverage — exactly what ABS reads today, in plain numbers.
//
// Three stacked sections:
//   1. Active jurisdiction hero (dark on light).
//   2. Bylaws table — the documents we have actually indexed end-to-end.
//   3. Methodology + Roadmap two-col split, with a dark CTA strip.
//
// Source of truth for the indexed-document set: the Layer 1 ingest
// manifest at abs-learning/output/halifax-regional-centre/manifest.json.
// If you add a new document here you should also add it to that manifest
// (or vice versa) — the two should agree.

import type { Metadata } from "next";
import { pageMetadata } from "@/lib/page-metadata";
import Link from "next/link";
import { Mono } from "@/components/mono";
import { Btn } from "@/components/btn";
import { HighlightWord } from "@/components/highlight-word";
import { Page, PageHead } from "@/components/marketing/page-shell";

type Bylaw = {
  id: string;
  name: string;
  version: string;
  pages: number;
  status: "CURRENT";
};

type RoadmapItem = {
  name: string;
  scope: string;
  stage: "QUEUED" | "EXPLORATORY";
  note: string;
};

const ACTIVE = {
  name: "Halifax Regional Centre",
  parent: "Halifax Regional Municipality",
  province: "Nova Scotia",
};

// Single-document scope today. Page count and amendment date are from
// the Layer 1 ingest manifest (abs-learning/output/halifax-regional-centre).
const BYLAWS: Bylaw[] = [
  {
    id: "RCLUB",
    name: "Regional Centre Land Use By-law",
    version: "Consolidated · last amended 2024-04-26 (Case 24469)",
    pages: 457,
    status: "CURRENT",
  },
];

const METHODOLOGY: Array<{ n: string; t: string; d: string }> = [
  {
    n: "01",
    t: "Acquire",
    d: "Source the consolidated bylaw text directly from the municipality's published version.",
  },
  {
    n: "02",
    t: "Parse",
    d: "Extract structure — parts, sections, subsections, clauses, schedules — into a fragment graph keyed to the official citation scheme.",
  },
  {
    n: "03",
    t: "Link to land",
    d: "Join the zoning layer to parcel geometry so a reading can resolve from a civic address to the rules that apply on the ground.",
  },
  {
    n: "04",
    t: "Test against real questions",
    d: "Run an evolving evaluator suite against the agent before opening new coverage. Beta means this is still in motion — see the Linear board.",
  },
];

const ROADMAP: RoadmapItem[] = [
  {
    name: "Rest of HRM (suburban + rural Land Use By-laws)",
    scope: "NS",
    stage: "QUEUED",
    note: "Same parser, additional documents. Sequenced after the Regional Centre LUB stabilizes.",
  },
  {
    name: "Other Atlantic Canada municipalities",
    scope: "ATL · CAN",
    stage: "EXPLORATORY",
    note: "Scoping by demand. If you want ABS in your city, tell us.",
  },
];

// Stage swatch colors map to design tokens.
const stageClass = (s: RoadmapItem["stage"]) =>
  s === "QUEUED" ? "text-accent-ink" : "text-text-muted";

const stageBgClass = (s: RoadmapItem["stage"]) =>
  s === "QUEUED" ? "bg-accent-ink" : "bg-text-muted opacity-35";

export const metadata: Metadata = pageMetadata({
  path: "/coverage",
  title: "Bylaw Coverage — ABS°",
  description:
    "Exactly which Halifax zoning rules ABS reads today: the Regional Centre Land Use By-law, indexed end to end, plus the HRM by-laws queued next.",
});

export default function CoveragePage() {
  return (
    <Page>
      <PageHead
        kicker="COVERAGE · PRIVATE BETA"
        title="One jurisdiction. Deep."
        sub="ABS is built for one place at a time. The page below lists exactly what's indexed today — no more, no less."
      />

      {/* Active hero — inverted card */}
      <section
        className="bg-text text-surface px-6 sm:px-8 py-7 sm:py-8 mb-3.5"
      >
        <div className="flex flex-wrap justify-between items-start gap-6">
          <div className="flex flex-col gap-1.5 flex-1 min-w-[280px]">
            <Mono
              size={10}
              style={{ color: "rgba(255,255,255,0.55)" }}
            >
              ACTIVE · {ACTIVE.province.toUpperCase()}
            </Mono>
            <h2
              className="font-sans m-0 text-[36px] sm:text-[48px] lg:text-[56px]"
              style={{
                fontWeight: 800,
                letterSpacing: "-0.04em",
                lineHeight: 0.95,
              }}
            >
              {ACTIVE.name}
            </h2>
            <div
              className="mt-1"
              style={{
                fontSize: 13,
                color: "rgba(255,255,255,0.6)",
                letterSpacing: "-0.005em",
              }}
            >
              The urban core of {ACTIVE.parent}. Other HRM plan areas are
              not yet in scope.
            </div>
          </div>
          <span
            className="bg-accent text-on-accent self-start font-mono"
            style={{
              padding: "5px 11px",
              fontSize: 9.5,
              letterSpacing: "0.14em",
            }}
          >
            PRIMARY BYLAW INDEXED
          </span>
        </div>

        <div
          className="grid grid-cols-1 sm:grid-cols-3 gap-6 mt-7 pt-[22px]"
          style={{ borderTop: "1px solid rgba(255,255,255,0.15)" }}
        >
          {[
            { l: "JURISDICTIONS", n: "1" },
            { l: "BYLAW DOCUMENTS", n: String(BYLAWS.length) },
            { l: "STATUS", n: "Private beta" },
          ].map((s) => (
            <div key={s.l}>
              <Mono size={9.5} style={{ color: "rgba(255,255,255,0.55)" }}>
                {s.l}
              </Mono>
              <div
                className="mt-1.5"
                style={{
                  fontSize: 28,
                  fontWeight: 700,
                  letterSpacing: "-0.025em",
                }}
              >
                {s.n}
              </div>
            </div>
          ))}
        </div>
      </section>

      {/* Bylaws table */}
      <section className="border border-hair mb-14">
        {/* Header — desktop only; on mobile each row is self-labeled */}
        <div
          className="hidden lg:grid bg-surface-alt border-b border-hair gap-4"
          style={{
            padding: "12px 18px",
            gridTemplateColumns: "2.4fr 2.4fr 0.8fr 0.8fr",
          }}
        >
          {["DOCUMENT", "VERSION", "PAGES", "STATUS"].map((h) => (
            <Mono muted size={9.5} key={h}>
              {h}
            </Mono>
          ))}
        </div>
        {BYLAWS.map((b, i) => (
          <div
            key={b.id}
            className={
              "lg:grid flex flex-col gap-3 lg:gap-4 lg:items-center text-[13.5px]" +
              (i < BYLAWS.length - 1 ? " border-b border-hair" : "")
            }
            style={{
              padding: "14px 18px",
              gridTemplateColumns: "2.4fr 2.4fr 0.8fr 0.8fr",
            }}
          >
            <div className="flex flex-col gap-0.5">
              <span className="font-semibold" style={{ letterSpacing: "-0.01em" }}>
                {b.name}
              </span>
              <Mono muted size={9}>
                {b.id}
              </Mono>
            </div>
            <span className="text-text-muted" style={{ fontSize: 12.5 }}>
              {b.version}
            </span>
            <span className="font-mono" style={{ fontSize: 12 }}>
              <span className="lg:hidden text-text-muted mr-2">PAGES</span>
              {b.pages}
            </span>
            <Mono accent size={9.5}>
              {b.status}
            </Mono>
          </div>
        ))}
      </section>

      {/* Methodology + Roadmap */}
      <div className="grid grid-cols-1 lg:[grid-template-columns:1fr_1.2fr] gap-10 lg:gap-14 items-start">
        {/* Methodology */}
        <section>
          <Mono muted size={10} className="mb-3 block">
            HOW WE COVER A JURISDICTION
          </Mono>
          <h3
            className="m-0 mb-[22px] font-sans"
            style={{
              fontSize: 32,
              fontWeight: 700,
              letterSpacing: "-0.03em",
              lineHeight: 1.05,
            }}
          >
            We don't claim coverage
            <br />
            until it's <HighlightWord>verifiable</HighlightWord>.
          </h3>
          <ol className="list-none p-0 m-0 flex flex-col gap-[18px]">
            {METHODOLOGY.map((s) => (
              <li
                key={s.n}
                className="grid gap-3.5 items-start"
                style={{ gridTemplateColumns: "50px 1fr" }}
              >
                <Mono accent size={11}>
                  {s.n}
                </Mono>
                <div>
                  <div
                    className="mb-1"
                    style={{
                      fontSize: 16,
                      fontWeight: 600,
                      letterSpacing: "-0.015em",
                    }}
                  >
                    {s.t}
                  </div>
                  <div
                    className="text-text-muted"
                    style={{ fontSize: 13.5, lineHeight: 1.5 }}
                  >
                    {s.d}
                  </div>
                </div>
              </li>
            ))}
          </ol>
        </section>

        {/* Roadmap */}
        <section>
          <Mono muted size={10} className="mb-3 block">
            ROADMAP · DIRECTIONAL
          </Mono>
          <h3
            className="m-0 mb-[22px] font-sans"
            style={{
              fontSize: 32,
              fontWeight: 700,
              letterSpacing: "-0.03em",
              lineHeight: 1.05,
            }}
          >
            What's next.
          </h3>
          <div className="flex flex-col gap-2.5">
            {ROADMAP.map((r) => (
              <div
                key={r.name}
                className="grid gap-3 sm:gap-4 items-start border border-hair bg-surface-alt"
                style={{
                  padding: "14px 16px",
                  gridTemplateColumns: "1.6fr 0.6fr 0.8fr",
                }}
              >
                <div>
                  <div
                    style={{
                      fontSize: 15.5,
                      fontWeight: 600,
                      letterSpacing: "-0.015em",
                      lineHeight: 1.3,
                    }}
                  >
                    {r.name}
                  </div>
                  <div
                    className="text-text-muted mt-1"
                    style={{ fontSize: 12, lineHeight: 1.5 }}
                  >
                    {r.note}
                  </div>
                </div>
                <Mono muted size={9.5}>
                  {r.scope}
                </Mono>
                <div className="flex items-center gap-2 justify-end">
                  <span
                    className={"font-mono " + stageClass(r.stage)}
                    style={{ fontSize: 9.5, letterSpacing: "0.12em" }}
                  >
                    {r.stage}
                  </span>
                  <span
                    className={"inline-block w-2 h-2 " + stageBgClass(r.stage)}
                  />
                </div>
              </div>
            ))}
          </div>
          <div
            className="mt-3 text-text-muted"
            style={{ fontSize: 12, lineHeight: 1.5 }}
          >
            No fixed ETAs while ABS is in beta — we'd rather ship coverage
            when the evaluator says it's ready than commit to a quarter.
          </div>
          <div
            className="mt-5 bg-text text-surface flex justify-between items-center gap-4 flex-wrap"
            style={{ padding: "14px 16px" }}
          >
            <div>
              <div style={{ fontSize: 15, fontWeight: 600 }}>
                Want ABS in your city?
              </div>
              <div
                className="mt-0.5"
                style={{
                  fontSize: 12.5,
                  color: "rgba(255,255,255,0.7)",
                }}
              >
                Tell us. We weight the roadmap by demand.
              </div>
            </div>
            <Link href="/support">
              <Btn variant="accent" size="sm">
                Request →
              </Btn>
            </Link>
          </div>
        </section>
      </div>
    </Page>
  );
}
