// /support — beta-era support page.
//
// We don't have a help center, live chat, status dashboard, or office
// hours yet. This page lists the two real channels (email + the in-app
// flag on a bad reading) and says so honestly.

import Link from "next/link";
import { Mono } from "@/components/mono";
import { Page, PageHead } from "@/components/marketing/page-shell";

const FAQS: Array<{ q: string; a: string }> = [
  {
    q: "Is ABS giving me legal advice?",
    a: "No. ABS is a research tool — it retrieves bylaw text and reasons about it. Always verify a reading with HRM Planning before relying on it for a permit application or any irreversible decision. The Terms page covers this in detail.",
  },
  {
    q: "What does ABS actually cover today?",
    a: "The Halifax Regional Centre Land Use By-law. That's the single document in scope during private beta. The Coverage page lists the indexed document and what's queued next.",
  },
  {
    q: "How do I report a bad reading?",
    a: "Use the flag control inside the reading itself — it sends the full context (question, parcel, citations, agent trace) to us. That's the fastest way to get a fix or, where the agent is right and just hard to read, a clarification.",
  },
  {
    q: "How is billing set up right now?",
    a: "You pay per answer: pick a question from the menu, see its price up front, and buy just that answer (the Pricing page lists them). Opening a case is free. On this deployment the purchase flow may be dormant — beta accounts are typically granted access manually. See the Billing page once signed in.",
  },
];

export default function SupportPage() {
  return (
    <Page>
      <PageHead
        kicker="SUPPORT · PRIVATE BETA"
        title="How can we help?"
        sub="ABS is in private beta. There's no help center yet — the two real channels are email and the flag control inside a reading."
      />

      {/* Contact panel */}
      <section className="grid grid-cols-1 lg:grid-cols-2 gap-3.5 mb-14">
        <a
          href="mailto:info@agenticbylawsystems.com"
          className="no-underline text-text border-[1.5px] border-text bg-surface flex flex-col gap-2"
          style={{ padding: "22px 26px" }}
        >
          <Mono muted size={10}>
            EMAIL · BEST FOR BETA QUESTIONS
          </Mono>
          <div
            style={{
              fontSize: 24,
              fontWeight: 700,
              letterSpacing: "-0.02em",
              lineHeight: 1.1,
            }}
          >
            info@agenticbylawsystems.com
          </div>
          <p
            className="m-0 text-text-muted"
            style={{ fontSize: 13.5, lineHeight: 1.5 }}
          >
            Account questions, billing, access, feedback, "is this in
            scope" — anything that isn't a specific bad reading goes here.
            One person reads this inbox; expect a reply within a few
            business days during beta.
          </p>
        </a>

        <div
          className="border border-hair bg-surface-alt flex flex-col gap-2"
          style={{ padding: "22px 26px" }}
        >
          <Mono muted size={10}>
            IN-APP FLAG · BEST FOR BAD READINGS
          </Mono>
          <div
            style={{
              fontSize: 24,
              fontWeight: 700,
              letterSpacing: "-0.02em",
              lineHeight: 1.1,
            }}
          >
            ⚑ Flag from inside the reading
          </div>
          <p
            className="m-0 text-text-muted"
            style={{ fontSize: 13.5, lineHeight: 1.5 }}
          >
            If a reading is wrong — bad citation, wrong parcel match,
            invented section, applied a rule that doesn't apply — use the
            flag control in the reading. It captures the full agent trace,
            which is what we need to actually reproduce and fix it.
          </p>
        </div>
      </section>

      {/* FAQs */}
      <section>
        <Mono muted size={10} className="mb-3.5 block">
          FREQUENTLY ASKED · {String(FAQS.length).padStart(2, "0")}
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
          The questions that come up most.
        </h3>
        <ul className="list-none p-0 m-0 flex flex-col">
          {FAQS.map((f, i) => (
            <li
              key={i}
              className="grid items-start border-t border-hair gap-4 py-5"
              style={{ gridTemplateColumns: "40px 1fr" }}
            >
              <Mono muted size={11}>
                {String(i + 1).padStart(2, "0")}
              </Mono>
              <div className="flex flex-col gap-2">
                <div
                  style={{
                    fontSize: 16,
                    fontWeight: 600,
                    letterSpacing: "-0.015em",
                    lineHeight: 1.3,
                  }}
                >
                  {f.q}
                </div>
                <div
                  className="text-text-muted"
                  style={{ fontSize: 14, lineHeight: 1.55 }}
                >
                  {f.a}
                </div>
              </div>
            </li>
          ))}
        </ul>
      </section>

      {/* What we don't have */}
      <section
        className="mt-12 border border-dashed border-hair"
        style={{ padding: "20px 22px" }}
      >
        <Mono muted size={9.5} className="block mb-2">
          WHAT WE DON'T HAVE YET
        </Mono>
        <p
          className="m-0 text-text-muted"
          style={{ fontSize: 13.5, lineHeight: 1.55 }}
        >
          No published help center, no in-app live chat, no public status
          dashboard, no office hours. Those will come after GA. For now
          email and the in-app flag are the channels we monitor.
        </p>
      </section>

      {/* Closing pointer */}
      <section className="mt-10 flex flex-wrap gap-3 items-center text-text-muted text-[13.5px]">
        Looking for the legal docs? See the{" "}
        <Link href="/terms" className="text-text underline">
          Terms
        </Link>{" "}
        and{" "}
        <Link href="/privacy" className="text-text underline">
          Privacy
        </Link>{" "}
        pages.
      </section>
    </Page>
  );
}
