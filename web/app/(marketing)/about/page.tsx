// /about — company mission, principles, timeline, place, team, CTA.
//
// Five sections from the design handoff. The Halifax dark card uses an
// inline bar graphic (20 plain divs, every 5th lime per spec). Portraits
// are CSS-only: first card solid accent, rest striped diagonal lines.

import Link from "next/link";
import { Mono } from "@/components/mono";
import { Btn } from "@/components/btn";
import { HighlightWord } from "@/components/highlight-word";
import { Page, PageHead } from "@/components/marketing/page-shell";

const PRINCIPLES = [
  {
    n: "01",
    t: "A reading must be sourced.",
    d: "Every assertion carries a citation. Every citation links to the exact paragraph in the consolidated bylaw, dated.",
  },
  {
    n: "02",
    t: "The agent should refuse before it should guess.",
    d: "When two clauses conflict, ABS surfaces both. When the parcel data is uncertain, the answer says so out loud.",
  },
  {
    n: "03",
    t: "Depth before breadth.",
    d: "One jurisdiction, indexed end-to-end, calibrated against planner-reviewed answers. Then the next.",
  },
  {
    n: "04",
    t: "Built with planners, not around them.",
    d: "HRM Planning reviews our test set. We do not pretend to replace the development officer — we make the conversation faster.",
  },
];

const TIMELINE = [
  {
    d: "Aug 2024",
    t: "Founded in Halifax, in a kitchen above the Hydrostone market.",
  },
  { d: "Nov 2024", t: "First indexable parse of the HRM Land Use By-law." },
  { d: "Feb 2025", t: "Pre-seed round closed. Hired Sana to lead retrieval." },
  { d: "Jul 2025", t: "First planner-validated reading." },
  { d: "Feb 2026", t: "Private beta opens to architects in HRM." },
];

const TEAM = [
  {
    name: "Mira Caulfield",
    role: "Co-founder · Eng",
    loc: "Halifax, NS",
    past: "Prev. mapping at Esri Canada",
  },
  {
    name: "Aaron Pictou",
    role: "Co-founder · Planning",
    loc: "Dartmouth, NS",
    past: "Prev. HRM Development Officer",
  },
  {
    name: "Sana Ng",
    role: "Retrieval & ML",
    loc: "Halifax, NS",
    past: "Prev. Cohere",
  },
  {
    name: "Joel Demaine",
    role: "Design",
    loc: "St. John’s, NL",
    past: "Prev. independent",
  },
];

// Harbour bar heights from the spec. Every 5th bar is the accent lime.
const HARBOUR_BARS = [
  18, 32, 28, 46, 36, 58, 42, 54, 38, 72, 50, 64, 48, 56, 40, 42, 30, 24, 18,
  14,
];

export default function AboutPage() {
  return (
    <Page>
      <PageHead
        kicker="ABOUT · ABS"
        title="An expert planner, in your workflow."
        sub="ABS is a small team building one thing: an agent that reads municipal by-laws accurately, against a real parcel, with sources you can hand to a development officer."
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
          <HighlightWord height={0.16}>too slow to read</HighlightWord>. We’re
          fixing the reading.
        </p>
      </section>

      {/* Principles */}
      <section className="mb-16 lg:mb-[72px]">
        <div className="flex items-center gap-3.5 mb-6">
          <Mono muted size={10}>
            PRINCIPLES · 04 IN FORCE
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

      {/* Origin + Timeline */}
      <section className="grid grid-cols-1 lg:[grid-template-columns:1.2fr_1fr] gap-10 lg:gap-14 mb-16 lg:mb-[72px] items-start">
        <div>
          <Mono muted size={10} className="mb-3.5 block">
            ORIGIN
          </Mono>
          <h3
            className="m-0 mb-[22px] font-sans"
            style={{
              fontSize: 36,
              fontWeight: 700,
              letterSpacing: "-0.03em",
              lineHeight: 1.05,
            }}
          >
            We started with one question:
            <br />
            “What if reading a bylaw was as fast as building one?”
          </h3>
          <div
            className="flex flex-col gap-3.5 max-w-[560px] text-text"
            style={{ fontSize: 15, lineHeight: 1.6 }}
          >
            <p className="m-0">
              ABS began in Halifax, where the founders kept watching
              small-builder projects stall in the planner queue. The bylaw
              wasn’t unclear — it just wasn’t searchable.
            </p>
            <p className="m-0">
              We started with one document, one zone, one address. Then a
              hundred. Now the entire HRM Land Use By-law system, with the
              same standard of evidence behind every answer.
            </p>
          </div>
        </div>

        <div className="border border-hair">
          <div className="bg-surface-alt border-b border-hair flex justify-between px-4 py-3.5">
            <Mono muted size={10}>
              TIMELINE
            </Mono>
            <Mono muted size={10}>
              2024 — NOW
            </Mono>
          </div>
          {TIMELINE.map((m, i) => (
            <div
              key={m.d}
              className={
                "grid items-start gap-3.5 px-4 py-3.5 " +
                (i < TIMELINE.length - 1 ? "border-b border-hair" : "")
              }
              style={{ gridTemplateColumns: "100px 1fr" }}
            >
              <span
                className="font-mono text-text-muted pt-0.5"
                style={{ fontSize: 10.5, letterSpacing: "0.06em" }}
              >
                {m.d.toUpperCase()}
              </span>
              <span style={{ fontSize: 13.5, lineHeight: 1.5 }}>{m.t}</span>
            </div>
          ))}
        </div>
      </section>

      {/* Halifax dark card */}
      <section
        className="bg-text text-surface px-7 sm:px-9 py-10 mb-16 lg:mb-[72px] grid grid-cols-1 lg:grid-cols-2 gap-9 items-center"
      >
        <div className="flex flex-col gap-3.5">
          <Mono size={10} style={{ color: "rgba(255,255,255,0.55)" }}>
            WHERE WE ARE · 44.6488° N, 63.5752° W
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
            We started here on purpose. Halifax is small enough to learn
            end-to-end, big enough to matter — and we walk past the buildings
            ABS has read every day.
          </p>
        </div>

        {/* Harbour graphic */}
        <div
          className="flex flex-col justify-between"
          style={{
            aspectRatio: "4 / 2.2",
            background: "rgba(255,255,255,0.04)",
            border: "1px solid rgba(255,255,255,0.18)",
            padding: 18,
          }}
        >
          <div className="flex justify-between">
            <Mono size={9} style={{ color: "rgba(255,255,255,0.55)" }}>
              HRM
            </Mono>
            <Mono size={9} style={{ color: "rgba(255,255,255,0.55)" }}>
              5,490 km²
            </Mono>
          </div>
          <div
            style={{
              height: 1,
              background: "rgba(255,255,255,0.25)",
              margin: "0 -18px",
            }}
          />
          <div
            className="flex items-end"
            style={{ gap: 1, height: 64 }}
            aria-hidden
          >
            {HARBOUR_BARS.map((h, i) => (
              <div
                key={i}
                style={{
                  flex: 1,
                  height: `${h}px`,
                  background:
                    i % 5 === 0 ? "var(--accent)" : "rgba(255,255,255,0.55)",
                }}
              />
            ))}
          </div>
          <div
            style={{
              height: 1,
              background: "rgba(255,255,255,0.25)",
              margin: "0 -18px",
            }}
          />
          <div className="flex justify-between">
            <Mono size={9} style={{ color: "rgba(255,255,255,0.55)" }}>
              PENINSULA
            </Mono>
            <Mono size={9} style={{ color: "rgba(255,255,255,0.55)" }}>
              DARTMOUTH
            </Mono>
            <Mono size={9} style={{ color: "rgba(255,255,255,0.55)" }}>
              BEDFORD
            </Mono>
            <Mono size={9} style={{ color: "rgba(255,255,255,0.55)" }}>
              SACKVILLE
            </Mono>
          </div>
        </div>
      </section>

      {/* Team */}
      <section className="mb-6">
        <div className="flex justify-between items-baseline mb-6 flex-wrap gap-3">
          <div>
            <Mono muted size={10} className="mb-2 block">
              PEOPLE · 04 + GROWING
            </Mono>
            <h3
              className="m-0 font-sans"
              style={{
                fontSize: 32,
                fontWeight: 700,
                letterSpacing: "-0.03em",
                lineHeight: 1.05,
              }}
            >
              The team.
            </h3>
          </div>
          <Btn variant="ghost" size="sm">
            We’re hiring →
          </Btn>
        </div>
        <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-3.5">
          {TEAM.map((p, i) => (
            <div
              key={p.name}
              className="border border-hair flex flex-col"
            >
              <div
                className={
                  "relative flex items-end overflow-hidden " +
                  (i === 0 ? "bg-accent" : "bg-surface-alt")
                }
                style={{
                  aspectRatio: "1 / 1",
                  padding: 12,
                  backgroundImage:
                    i === 0
                      ? "none"
                      : "repeating-linear-gradient(45deg, var(--hair) 0 1px, transparent 1px 12px)",
                }}
              >
                <Mono
                  size={9}
                  style={{
                    color: i === 0 ? "var(--on-accent)" : "var(--text-muted)",
                  }}
                >
                  PORTRAIT · {i + 1}/4
                </Mono>
              </div>
              <div className="border-t border-hair flex flex-col gap-1 px-4 py-4">
                <div
                  style={{
                    fontSize: 16,
                    fontWeight: 700,
                    letterSpacing: "-0.015em",
                  }}
                >
                  {p.name}
                </div>
                <div className="text-text" style={{ fontSize: 13 }}>
                  {p.role}
                </div>
                <div
                  className="mt-1.5 text-text-muted"
                  style={{ fontSize: 12 }}
                >
                  {p.past}
                </div>
                <Mono muted size={9} className="mt-1.5 block">
                  {p.loc.toUpperCase()}
                </Mono>
              </div>
            </div>
          ))}
        </div>
      </section>

      {/* Closing CTA */}
      <section className="mt-14 px-6 sm:px-8 py-7 border-[1.5px] border-text flex justify-between items-center gap-6 flex-wrap">
        <div className="flex flex-col gap-1">
          <Mono muted size={10}>
            SAY HELLO
          </Mono>
          <div
            style={{
              fontSize: 22,
              fontWeight: 700,
              letterSpacing: "-0.02em",
            }}
          >
            hello@abs.app · @abs.halifax
          </div>
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
