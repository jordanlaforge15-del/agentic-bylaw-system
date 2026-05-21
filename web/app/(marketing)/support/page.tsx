// /support — help center entry.
//
// Sections:
//   1. Search input + system-status panel.
//   2. 2×2 categories grid.
//   3. Popular articles + contact panel.
//
// Search is client-side (controlled input) but submit is a no-op for
// now; the design handoff explicitly notes search wiring is a follow-up.

"use client";

import { useState } from "react";
import { Mono } from "@/components/mono";
import { Page, PageHead } from "@/components/marketing/page-shell";
import { cn } from "@/lib/cn";

const CATEGORIES: Array<{
  icon: string;
  name: string;
  sub: string;
  articles: string[];
}> = [
  {
    icon: "◐",
    name: "Readings",
    sub: "How the agent answers",
    articles: [
      "What counts as a reading?",
      "Why does ABS sometimes refuse to answer?",
      "Understanding confidence scores",
    ],
  },
  {
    icon: "◧",
    name: "Parcels & maps",
    sub: "Address resolution & zoning",
    articles: [
      "My address resolved to the wrong parcel",
      "Parcels straddling two zones",
      "What is the difference between LUB and Centre Plan?",
    ],
  },
  {
    icon: "◨",
    name: "Export & sharing",
    sub: "Permit-ready exports",
    articles: [
      "Exporting a reading to PDF",
      "Sharing a thread with your team",
      "Adding a verifier sign-off",
    ],
  },
  {
    icon: "◓",
    name: "Account & billing",
    sub: "Seats, invoices, plans",
    articles: [
      "Adding a seat to your workspace",
      "Switching from Drafter to Practice",
      "Updating your billing email",
    ],
  },
];

const POPULAR: Array<{ title: string; mins: string; tag: string }> = [
  { title: "How accurate is ABS, really?", mins: "4 min read", tag: "METHOD" },
  {
    title: "What ABS is not: this is research, not legal advice",
    mins: "2 min read",
    tag: "POLICY",
  },
  {
    title: "My answer cites a section that doesn’t apply — what now?",
    mins: "3 min read",
    tag: "READINGS",
  },
  {
    title: "Subletting an answer to a development officer",
    mins: "5 min read",
    tag: "EXPORT",
  },
];

export default function SupportPage() {
  const [q, setQ] = useState("");

  return (
    <Page>
      <PageHead
        kicker="SUPPORT · HRM PRIVATE BETA"
        title="How can we help?"
        sub="Most things you’ll want are below. If not, a human on the team replies within one business day during beta."
      />

      {/* Search + status row */}
      <div className="grid grid-cols-1 lg:[grid-template-columns:1.6fr_1fr] gap-3.5 mb-10">
        <form
          onSubmit={(e) => e.preventDefault()}
          className="flex border-[1.5px] border-text bg-surface"
        >
          <span
            className="px-3.5 flex items-center font-mono text-text-muted"
            style={{ fontSize: 12, letterSpacing: "0.08em" }}
          >
            SEARCH ›
          </span>
          <input
            value={q}
            onChange={(e) => setQ(e.target.value)}
            placeholder="e.g. backyard suite, frontage, export to PDF"
            aria-label="Search support articles"
            className="flex-1 py-3.5 px-0 bg-transparent text-text outline-none border-0"
            style={{ fontSize: 15, letterSpacing: "-0.005em" }}
          />
          <button
            type="submit"
            className="bg-text text-surface font-sans font-bold cursor-pointer border-0"
            style={{ padding: "0 22px", fontSize: 14 }}
          >
            Search →
          </button>
        </form>

        <div className="border border-hair bg-surface-alt flex items-center gap-3.5 px-4 py-3">
          <span
            className="abs-pulse-dot bg-accent shrink-0"
            style={{ width: 10, height: 10 }}
          />
          <div className="flex-1">
            <Mono muted size={9.5}>
              SYSTEM STATUS
            </Mono>
            <div
              className="mt-0.5"
              style={{ fontSize: 14, fontWeight: 600 }}
            >
              All systems operational
            </div>
          </div>
          <Mono muted size={9.5}>
            247 ms · p95
          </Mono>
        </div>
      </div>

      {/* Categories */}
      <Mono muted size={10} className="mb-3.5 block">
        BROWSE · 4 AREAS
      </Mono>
      <div className="grid grid-cols-1 sm:grid-cols-2 gap-3.5 mb-14">
        {CATEGORIES.map((c) => (
          <div
            key={c.name}
            className="bg-surface border-[1.5px] border-text px-6 py-6 flex flex-col gap-3.5"
          >
            <div className="flex justify-between items-start">
              <div>
                <div
                  style={{
                    fontSize: 26,
                    fontWeight: 700,
                    letterSpacing: "-0.025em",
                    lineHeight: 1.1,
                  }}
                >
                  {c.name}
                </div>
                <div
                  className="mt-1 text-text-muted"
                  style={{ fontSize: 13 }}
                >
                  {c.sub}
                </div>
              </div>
              <span
                className="text-accent-ink font-mono leading-none"
                style={{ fontSize: 36 }}
                aria-hidden
              >
                {c.icon}
              </span>
            </div>
            <ul className="list-none p-0 m-0 flex flex-col border-t border-hair">
              {c.articles.map((a) => (
                <li
                  key={a}
                  className={cn(
                    "flex justify-between items-center cursor-pointer border-b border-hair",
                    "py-2.5",
                  )}
                  style={{ fontSize: 13.5 }}
                >
                  <span>{a}</span>
                  <span className="text-text-muted">→</span>
                </li>
              ))}
            </ul>
          </div>
        ))}
      </div>

      {/* Popular + Contact */}
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-10 lg:gap-14 items-start">
        <section>
          <Mono muted size={10} className="mb-3.5 block">
            POPULAR ARTICLES
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
            What people read first.
          </h3>
          <ul className="list-none p-0 m-0 flex flex-col">
            {POPULAR.map((a, i) => (
              <li
                key={i}
                className="grid items-center cursor-pointer border-t border-hair gap-4 py-4"
                style={{ gridTemplateColumns: "40px 1fr auto" }}
              >
                <Mono muted size={11}>
                  {String(i + 1).padStart(2, "0")}
                </Mono>
                <div className="flex flex-col gap-1">
                  <span
                    style={{
                      fontSize: 15.5,
                      fontWeight: 600,
                      letterSpacing: "-0.015em",
                    }}
                  >
                    {a.title}
                  </span>
                  <div className="flex gap-2.5">
                    <Mono muted size={9.5}>
                      {a.tag}
                    </Mono>
                    <Mono muted size={9.5}>
                      · {a.mins.toUpperCase()}
                    </Mono>
                  </div>
                </div>
                <span className="text-text-muted">→</span>
              </li>
            ))}
          </ul>
        </section>

        <section className="bg-surface-alt border border-hair p-7 flex flex-col gap-[22px]">
          <Mono muted size={10}>
            STILL STUCK?
          </Mono>
          <h3
            className="m-0 font-sans"
            style={{
              fontSize: 28,
              fontWeight: 700,
              letterSpacing: "-0.025em",
              lineHeight: 1.1,
            }}
          >
            Talk to a human.
          </h3>
          <p
            className="m-0 text-text-muted"
            style={{ fontSize: 14, lineHeight: 1.5 }}
          >
            During private beta we read every message. Average first reply: 3
            hours during HRM business hours.
          </p>

          <div className="flex flex-col gap-2.5">
            <a
              href="mailto:hello@abs.app"
              className="no-underline text-text"
            >
              <div className="flex justify-between items-center bg-surface border border-hair px-4 py-3.5">
                <div>
                  <Mono muted size={9.5}>
                    EMAIL
                  </Mono>
                  <div
                    className="mt-0.5"
                    style={{ fontSize: 14, fontWeight: 600 }}
                  >
                    hello@abs.app
                  </div>
                </div>
                <span>→</span>
              </div>
            </a>
            <div className="flex justify-between items-center bg-surface border border-hair px-4 py-3.5 cursor-pointer">
              <div>
                <Mono muted size={9.5}>
                  IN-APP CHAT
                </Mono>
                <div
                  className="mt-0.5"
                  style={{ fontSize: 14, fontWeight: 600 }}
                >
                  Open chat in ABS
                </div>
              </div>
              <Mono accent size={10}>
                ONLINE
              </Mono>
            </div>
            <div className="flex justify-between items-center bg-surface border border-hair px-4 py-3.5 cursor-pointer">
              <div>
                <Mono muted size={9.5}>
                  OFFICE HOURS
                </Mono>
                <div
                  className="mt-0.5"
                  style={{ fontSize: 14, fontWeight: 600 }}
                >
                  Thursdays, 11am AT
                </div>
              </div>
              <span className="text-text-muted">→</span>
            </div>
          </div>

          <div
            className="pt-3.5 border-t border-hair text-text-muted"
            style={{ fontSize: 12, lineHeight: 1.5 }}
          >
            Reporting a bad reading? Use the ⚑ flag in the reading itself — it
            routes to engineering with full context.
          </div>
        </section>
      </div>
    </Page>
  );
}
