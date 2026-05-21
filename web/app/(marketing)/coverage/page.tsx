// /coverage — what jurisdictions ABS has indexed end-to-end.
//
// Three stacked sections:
//   1. Active jurisdiction hero (dark on light) with 4-stat strip.
//   2. Bylaws table (1px hair, 6-col grid at desktop; rows collapse to
//      stacked cards below `lg`).
//   3. Methodology + Roadmap two-col split, with a dark CTA strip
//      under the roadmap.

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
  fragments: string;
  status: "CURRENT" | "REFERENCE";
  spatial: boolean;
};

type RoadmapItem = {
  name: string;
  province: string;
  eta: string;
  stage: "INDEXING" | "NEGOTIATING" | "BACKLOG";
  note: string;
};

const ACTIVE = {
  name: "Halifax Regional Municipality",
  province: "Nova Scotia",
  parcels: "38,420",
  documents: 6,
  totalFragments: "18,142",
  lastSync: "2026-05-12",
};

const BYLAWS: Bylaw[] = [
  {
    id: "LUB-MAINLAND",
    name: "Land Use By-law for Halifax Mainland",
    version: "Consolidated · Mar 2026",
    pages: 248,
    fragments: "4,210",
    status: "CURRENT",
    spatial: true,
  },
  {
    id: "LUB-PENINSULA",
    name: "Land Use By-law for Halifax Peninsula",
    version: "Consolidated · Mar 2026",
    pages: 214,
    fragments: "3,884",
    status: "CURRENT",
    spatial: true,
  },
  {
    id: "LUB-DARTMOUTH",
    name: "Land Use By-law for Dartmouth",
    version: "Consolidated · Feb 2026",
    pages: 196,
    fragments: "3,402",
    status: "CURRENT",
    spatial: true,
  },
  {
    id: "CDD",
    name: "Centre Plan — Package A",
    version: "Adopted · 2021, am. Jan 2026",
    pages: 312,
    fragments: "5,108",
    status: "CURRENT",
    spatial: true,
  },
  {
    id: "SUB-BYL",
    name: "Regional Subdivision By-law",
    version: "Consolidated · Nov 2025",
    pages: 88,
    fragments: "1,124",
    status: "CURRENT",
    spatial: false,
  },
  {
    id: "NS-BC",
    name: "NS Building Code references",
    version: "Edition 2020",
    pages: 36,
    fragments: "414",
    status: "REFERENCE",
    spatial: false,
  },
];

const METHODOLOGY: Array<{ n: string; t: string; d: string }> = [
  {
    n: "01",
    t: "Acquire",
    d: "Source the consolidated bylaw text directly from the municipality.",
  },
  {
    n: "02",
    t: "Parse",
    d: "Extract structure — parts, sections, tables, defined terms — into a fragment graph.",
  },
  {
    n: "03",
    t: "Link to land",
    d: "Join the zoning layer to parcel geometry. Validate against a sample of 50 known parcels.",
  },
  {
    n: "04",
    t: "Calibrate",
    d: "Run 1,000+ planner-reviewed test questions. Coverage launches only above 0.90 calibrated confidence.",
  },
];

const ROADMAP: RoadmapItem[] = [
  {
    name: "Charlottetown",
    province: "PE",
    eta: "Q3 2026",
    stage: "NEGOTIATING",
    note: "Bylaw acquisition in progress.",
  },
  {
    name: "Moncton",
    province: "NB",
    eta: "Q3 2026",
    stage: "INDEXING",
    note: "Documents acquired, parsing under review.",
  },
  {
    name: "Saint John",
    province: "NB",
    eta: "Q4 2026",
    stage: "INDEXING",
    note: "Documents acquired.",
  },
  {
    name: "Fredericton",
    province: "NB",
    eta: "Q4 2026",
    stage: "BACKLOG",
    note: "Sequenced after Moncton.",
  },
  {
    name: "St. John’s",
    province: "NL",
    eta: "Q1 2027",
    stage: "BACKLOG",
    note: "Targeted for Atlantic rollout.",
  },
  {
    name: "Sydney (CBRM)",
    province: "NS",
    eta: "Q1 2027",
    stage: "BACKLOG",
    note: "Following the Atlantic rollout.",
  },
];

// Stage swatch colors map to design tokens.
const stageClass = (s: RoadmapItem["stage"]) =>
  s === "INDEXING"
    ? "text-accent-ink"
    : s === "NEGOTIATING"
      ? "text-brick"
      : "text-text-muted";

const stageBgClass = (s: RoadmapItem["stage"]) =>
  s === "INDEXING"
    ? "bg-accent-ink"
    : s === "NEGOTIATING"
      ? "bg-brick"
      : "bg-text-muted opacity-35";

export default function CoveragePage() {
  return (
    <Page>
      <PageHead
        kicker="COVERAGE · MAY 2026"
        title="One jurisdiction. Deep."
        sub="ABS is built for one place at a time. We index every applicable bylaw end-to-end before opening a new region — so a reading isn’t half-true."
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
          </div>
          <span
            className="bg-accent text-on-accent self-start font-mono"
            style={{
              padding: "5px 11px",
              fontSize: 9.5,
              letterSpacing: "0.14em",
            }}
          >
            FULLY INDEXED
          </span>
        </div>

        <div
          className="grid grid-cols-2 sm:grid-cols-4 gap-6 mt-7 pt-[22px]"
          style={{ borderTop: "1px solid rgba(255,255,255,0.15)" }}
        >
          {[
            { l: "PARCELS", n: ACTIVE.parcels },
            { l: "BYLAW DOCUMENTS", n: ACTIVE.documents },
            { l: "TOTAL FRAGMENTS", n: ACTIVE.totalFragments },
            { l: "LAST SYNC", n: ACTIVE.lastSync },
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
            gridTemplateColumns:
              "2.4fr 1.6fr 0.8fr 0.8fr 0.7fr 0.6fr",
          }}
        >
          {["DOCUMENT", "VERSION", "PAGES", "FRAGMENTS", "SPATIAL", "STATUS"].map(
            (h) => (
              <Mono muted size={9.5} key={h}>
                {h}
              </Mono>
            ),
          )}
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
              gridTemplateColumns:
                "2.4fr 1.6fr 0.8fr 0.8fr 0.7fr 0.6fr",
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
            <span className="font-mono" style={{ fontSize: 12 }}>
              <span className="lg:hidden text-text-muted mr-2">FRAGMENTS</span>
              {b.fragments}
            </span>
            <span
              className={
                "font-mono " +
                (b.spatial ? "text-accent-ink" : "text-text-muted")
              }
              style={{ fontSize: 11, letterSpacing: "0.06em" }}
            >
              <span className="lg:hidden text-text-muted mr-2">SPATIAL</span>
              {b.spatial ? "✓ YES" : "— NO"}
            </span>
            <Mono
              accent={b.status === "CURRENT"}
              muted={b.status !== "CURRENT"}
              size={9.5}
            >
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
            We don’t claim coverage
            <br />
            until it’s <HighlightWord>verifiable</HighlightWord>.
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
            ROADMAP · ATLANTIC CANADA
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
            What’s next.
          </h3>
          <div className="flex flex-col gap-2.5">
            {ROADMAP.map((r) => (
              <div
                key={r.name}
                className="grid gap-3 sm:gap-4 items-center border border-hair bg-surface-alt"
                style={{
                  padding: "14px 16px",
                  gridTemplateColumns: "1.4fr 0.7fr 1fr",
                }}
              >
                <div>
                  <div
                    style={{
                      fontSize: 16,
                      fontWeight: 600,
                      letterSpacing: "-0.015em",
                    }}
                  >
                    {r.name}
                  </div>
                  <Mono muted size={9.5}>
                    {r.province}
                  </Mono>
                </div>
                <Mono muted size={10}>
                  ETA · {r.eta}
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
