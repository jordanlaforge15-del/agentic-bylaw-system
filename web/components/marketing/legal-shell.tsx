// Shared legal-document shell for /privacy and /terms.
//
// Visual concept: a bylaw document itself. Sticky sidebar TOC with §
// badges, plain-English summary card on top, sections numbered. Active
// section is tracked with an IntersectionObserver (replaces the
// scroll-listener pattern from the design prototype — same behavior,
// cheaper per scroll).

"use client";

import { useEffect, useState } from "react";
import { Mono } from "@/components/mono";
import { Page, PageHead } from "@/components/marketing/page-shell";
import { cn } from "@/lib/cn";

export type Block =
  | { k: "p"; v: string }
  | { k: "ul"; v: string[] }
  | { k: "note"; v: string };

export type Section = {
  id: string;
  n: string;
  t: string;
  body: Block[];
};

type Props = {
  kicker: string;
  title: string;
  sub: string;
  plainSummary: string;
  consolidatedAt: string;
  version: string;
  sections: Section[];
};

export function LegalShell({
  kicker,
  title,
  sub,
  plainSummary,
  consolidatedAt,
  version,
  sections,
}: Props) {
  const [active, setActive] = useState(sections[0].id);

  useEffect(() => {
    // Track the topmost section whose heading has scrolled past the
    // sticky-nav offset (140px). IntersectionObserver fires once per
    // boundary crossing, so we recompute the active id from scratch
    // each time rather than relying on the entry that fired.
    const headings = sections
      .map((s) => document.getElementById(s.id))
      .filter((el): el is HTMLElement => el !== null);

    const recompute = () => {
      let current = sections[0].id;
      for (const el of headings) {
        if (el.getBoundingClientRect().top < 140) current = el.id;
      }
      setActive(current);
    };

    recompute();

    const observer = new IntersectionObserver(recompute, {
      rootMargin: "-140px 0px -60% 0px",
      threshold: [0, 1],
    });
    headings.forEach((el) => observer.observe(el));
    return () => observer.disconnect();
  }, [sections]);

  return (
    <Page>
      <PageHead kicker={kicker} title={title} sub={sub} />

      {/* Plain-English summary */}
      <div
        className="bg-accent text-on-accent grid items-start gap-6 mb-10"
        style={{
          padding: "22px 26px",
          gridTemplateColumns: "minmax(0, 1fr)",
        }}
      >
        <div
          className="grid items-start gap-6"
          style={{ gridTemplateColumns: "160px 1fr" }}
        >
          <Mono size={10} style={{ color: "var(--on-accent)" }}>
            IN PLAIN ENGLISH
          </Mono>
          <div
            style={{
              fontSize: 17,
              fontWeight: 500,
              lineHeight: 1.45,
              letterSpacing: "-0.01em",
            }}
          >
            {plainSummary}
          </div>
        </div>
      </div>

      <div className="grid grid-cols-1 lg:[grid-template-columns:240px_1fr] gap-10 lg:gap-14 items-start">
        {/* Sidebar */}
        <aside className="flex flex-col lg:sticky lg:top-[92px]">
          <div className="pb-3.5 border-b border-hair mb-2.5">
            <Mono muted size={10}>
              CONSOLIDATED
            </Mono>
            <div
              className="mt-1"
              style={{ fontSize: 13.5, fontWeight: 600 }}
            >
              {consolidatedAt}
            </div>
            <Mono muted size={9.5} className="mt-1 block">
              VERSION {version}
            </Mono>
          </div>
          <Mono muted size={10} className="pb-2">
            CONTENTS
          </Mono>
          {sections.map((s) => (
            <a
              key={s.id}
              href={`#${s.id}`}
              onClick={() => setActive(s.id)}
              className={cn(
                "no-underline flex items-baseline gap-2 px-2.5 py-2 border-l-2",
                active === s.id
                  ? "text-text bg-surface-alt border-accent"
                  : "text-text-muted border-transparent",
              )}
              style={{ fontSize: 13, lineHeight: 1.35 }}
            >
              <span
                className="font-mono shrink-0"
                style={{
                  fontSize: 10.5,
                  letterSpacing: "0.04em",
                  minWidth: 24,
                }}
              >
                §{s.n}
              </span>
              <span style={{ fontWeight: active === s.id ? 600 : 400 }}>
                {s.t}
              </span>
            </a>
          ))}

        </aside>

        {/* Body */}
        <div className="flex flex-col gap-10 max-w-[720px]">
          {sections.map((s) => (
            <section
              id={s.id}
              key={s.id}
              style={{ scrollMarginTop: 96 }}
            >
              <header className="flex items-baseline gap-3.5 pb-3.5 border-b border-hair mb-[18px]">
                <Mono accent size={12}>
                  § {s.n}
                </Mono>
                <h2
                  className="m-0 font-sans"
                  style={{
                    fontSize: 28,
                    fontWeight: 700,
                    letterSpacing: "-0.025em",
                    lineHeight: 1.1,
                  }}
                >
                  {s.t}
                </h2>
              </header>
              <div
                className="flex flex-col gap-3.5 text-text"
                style={{ fontSize: 15, lineHeight: 1.6 }}
              >
                {s.body.map((b, i) => {
                  if (b.k === "p") {
                    return (
                      <p key={i} className="m-0">
                        {b.v}
                      </p>
                    );
                  }
                  if (b.k === "ul") {
                    return (
                      <ul
                        key={i}
                        className="m-0 pl-0 list-none flex flex-col gap-2"
                      >
                        {b.v.map((li, j) => (
                          <li
                            key={j}
                            className="grid items-start gap-1"
                            style={{ gridTemplateColumns: "24px 1fr" }}
                          >
                            <span
                              className="font-mono text-text-muted"
                              style={{ fontSize: 12 }}
                            >
                              ({String.fromCharCode(97 + j)})
                            </span>
                            <span>{li}</span>
                          </li>
                        ))}
                      </ul>
                    );
                  }
                  // note
                  return (
                    <div
                      key={i}
                      className="bg-surface-alt text-text"
                      style={{
                        padding: "12px 14px",
                        borderLeft: "2px solid var(--brick)",
                        fontSize: 13.5,
                        lineHeight: 1.55,
                      }}
                    >
                      <Mono
                        size={9.5}
                        className="block mb-1"
                        style={{ color: "var(--brick)" }}
                      >
                        NOTE
                      </Mono>
                      {b.v}
                    </div>
                  );
                })}
              </div>
            </section>
          ))}

          <div
            className="flex justify-between items-center gap-4 flex-wrap border border-dashed border-hair mt-6"
            style={{ padding: "20px 24px" }}
          >
            <div
              className="text-text-muted"
              style={{ fontSize: 13, lineHeight: 1.5 }}
            >
              Questions about this document? Email{" "}
              <a
                className="text-text font-semibold underline"
                href="mailto:info@agenticbylawsystems.com"
              >
                info@agenticbylawsystems.com
              </a>
              .
            </div>
            <Mono muted size={9.5}>
              END OF DOCUMENT
            </Mono>
          </div>
        </div>
      </div>
    </Page>
  );
}
