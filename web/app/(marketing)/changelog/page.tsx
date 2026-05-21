// /changelog — release history, modeled on a bylaw amendment register.
//
// Layout:
//   - desktop (lg ≥ 1024): sticky 220px version rail + entries column,
//     48px gap.
//   - tablet/mobile: rail collapses above the entries (no sticky).
//
// Each entry is an article. The first ("pinned") release gets the
// inverted treatment: surface-alt bg, 1.5px text border, lime LATEST
// badge clamped to the top-right corner. The rest are hair-bordered.
//
// TagPill is local to this page — three variants (NEW / IMPROVED /
// FIXED) per the design handoff.

import { Mono } from "@/components/mono";
import { Page, PageHead } from "@/components/marketing/page-shell";
import { cn } from "@/lib/cn";

type Tag = "NEW" | "IMPROVED" | "FIXED";

type Release = {
  v: string;
  date: string;
  label: string;
  pinned?: boolean;
  summary: string;
  changes: Array<{ tag: Tag; text: string }>;
};

const RELEASES: Release[] = [
  {
    v: "0.6.0",
    date: "2026-05-14",
    label: "Reading 2.0",
    pinned: true,
    summary:
      "A rewrite of how readings are composed. Citations now show provenance per clause, not per answer.",
    changes: [
      { tag: "NEW", text: "Per-clause citation overlays in the chat pane." },
      {
        tag: "NEW",
        text: "Conditional outcomes when a parcel triggers more than one zone.",
      },
      {
        tag: "IMPROVED",
        text: "Spatial retrieval re-ranks results against parcel boundary, not centroid.",
      },
      {
        tag: "IMPROVED",
        text: "Confidence model is now calibrated against 1,140 HRM planner-reviewed answers.",
      },
      {
        tag: "FIXED",
        text: "Frontage measurements no longer round before subtracting the corner cutback.",
      },
    ],
  },
  {
    v: "0.5.4",
    date: "2026-04-30",
    label: "Mainland LUB consolidation",
    summary:
      "Re-indexed the Land Use By-law for Halifax Mainland to the Mar 2026 consolidation.",
    changes: [
      {
        tag: "NEW",
        text: "Mar 2026 consolidation indexed. Old version still queryable as a historical snapshot.",
      },
      {
        tag: "NEW",
        text: "Inline diff between consolidations on any cited section.",
      },
      {
        tag: "IMPROVED",
        text: "Table extraction now handles the multi-row dimension cells in Part 9.",
      },
    ],
  },
  {
    v: "0.5.3",
    date: "2026-04-08",
    label: "Permit-ready exports",
    summary:
      "PDF exports now include a cover sheet, the reasoning trace, and a verifier sign-off field.",
    changes: [
      {
        tag: "NEW",
        text: "PDF cover sheet with parcel, zone, applicable bylaws.",
      },
      {
        tag: "NEW",
        text: "Verifier sign-off block — for a planner or your own QA.",
      },
      { tag: "FIXED", text: "Long answers were being truncated on export at 4 pages." },
    ],
  },
  {
    v: "0.5.2",
    date: "2026-03-19",
    label: "Saved parcels",
    summary:
      "You can pin a parcel to your sidebar. Readings made against a pinned parcel get grouped together.",
    changes: [
      { tag: "NEW", text: "Pin parcels to the sidebar." },
      { tag: "NEW", text: "Parcel-scoped reading history." },
      {
        tag: "IMPROVED",
        text: "Reading thread URLs are now permalinks — copy and share within your team.",
      },
    ],
  },
  {
    v: "0.5.1",
    date: "2026-02-28",
    label: "Faster cold reads",
    summary:
      "First reading on a parcel is 2.4× faster on a warm cache, 1.6× faster cold.",
    changes: [
      { tag: "IMPROVED", text: "Retrieval shards now keyed on zone, not just document." },
      { tag: "IMPROVED", text: "Reduced LLM round-trips in the planner step from 3 to 2." },
      {
        tag: "FIXED",
        text: "Edge case where parcels straddling two zones returned only one zone’s rules.",
      },
    ],
  },
  {
    v: "0.5.0",
    date: "2026-02-04",
    label: "Private beta open",
    summary: "First invites went out to architects and homeowners in HRM.",
    changes: [
      { tag: "NEW", text: "Invite-only signup live." },
      { tag: "NEW", text: "Practice plan with team workspaces." },
      { tag: "NEW", text: "HRM Land Use By-law (Mainland + Peninsula) indexed." },
    ],
  },
];

function TagPill({ tag }: { tag: Tag }) {
  // NEW: filled accent; IMPROVED: outlined text; FIXED: outlined hair muted.
  const variants: Record<Tag, string> = {
    NEW: "bg-accent text-on-accent border-accent",
    IMPROVED: "bg-transparent text-text border-text",
    FIXED: "bg-transparent text-text-muted border-hair",
  };
  return (
    <span
      className={cn(
        "inline-flex items-center justify-center font-mono uppercase",
        "min-w-[64px] px-1.5 py-[2px] border",
        variants[tag],
      )}
      style={{ fontSize: 9, letterSpacing: "0.14em" }}
    >
      {tag}
    </span>
  );
}

export default function ChangelogPage() {
  return (
    <Page>
      <PageHead
        kicker="REGISTER · CHANGELOG"
        title="What’s changed."
        sub="An amendment register for ABS itself. Every release lists what we added, fixed, and re-indexed against HRM’s by-laws."
      />

      <div className="grid grid-cols-1 lg:[grid-template-columns:220px_1fr] gap-8 lg:gap-12 items-start">
        {/* Version rail */}
        <aside className="flex flex-col lg:sticky lg:top-[92px] border-l border-hair">
          <Mono muted size={10} className="px-3.5 pt-0 pb-3">
            VERSIONS · {RELEASES.length}
          </Mono>
          {RELEASES.map((r, i) => (
            <a
              key={r.v}
              href={`#changelog-${r.v}`}
              className={cn(
                "flex justify-between items-baseline px-3.5 py-2.5 no-underline text-text -ml-px",
                i === 0 ? "border-l-2 border-accent" : "border-l-2 border-transparent",
              )}
            >
              <span
                className="font-mono"
                style={{
                  fontSize: 12,
                  fontWeight: i === 0 ? 600 : 400,
                  letterSpacing: "0.02em",
                }}
              >
                v{r.v}
              </span>
              <span
                className="font-mono text-text-muted"
                style={{ fontSize: 9.5, letterSpacing: "0.06em" }}
              >
                {r.date.slice(5)}
              </span>
            </a>
          ))}
          <div className="mt-2 p-3.5 bg-surface-alt text-text-muted text-[12px] leading-[1.45]">
            Want every release in your inbox?{" "}
            <a href="/signup" className="text-text underline">
              Subscribe
            </a>
            .
          </div>
        </aside>

        {/* Entries */}
        <div className="flex flex-col gap-9">
          {RELEASES.map((r) => (
            <article
              key={r.v}
              id={`changelog-${r.v}`}
              className={cn(
                "relative flex flex-col gap-[18px]",
                r.pinned
                  ? "bg-surface-alt border-[1.5px] border-text p-6 sm:p-7"
                  : "border border-hair p-5 sm:p-6",
              )}
              style={{ scrollMarginTop: 96 }}
            >
              {r.pinned && (
                <span
                  className="absolute -top-px -right-px bg-accent text-on-accent font-mono"
                  style={{
                    padding: "4px 10px",
                    fontSize: 9.5,
                    letterSpacing: "0.14em",
                  }}
                >
                  LATEST
                </span>
              )}

              <header className="flex flex-col gap-2 pb-4 border-b border-hair">
                <div className="flex justify-between items-baseline gap-3 flex-wrap">
                  <div className="flex items-baseline gap-3.5">
                    <span
                      className="font-mono text-accent-ink"
                      style={{
                        fontSize: 14,
                        fontWeight: 600,
                        letterSpacing: "0.04em",
                      }}
                    >
                      v{r.v}
                    </span>
                    <h2
                      className="font-sans m-0"
                      style={{
                        fontSize: 32,
                        fontWeight: 700,
                        letterSpacing: "-0.03em",
                        lineHeight: 1.05,
                      }}
                    >
                      {r.label}
                    </h2>
                  </div>
                  <Mono muted size={10}>
                    RELEASED · {r.date}
                  </Mono>
                </div>
                <p
                  className="text-text-muted m-0 max-w-[640px]"
                  style={{ fontSize: 14.5, lineHeight: 1.5 }}
                >
                  {r.summary}
                </p>
              </header>

              <ul className="list-none p-0 m-0 flex flex-col gap-2">
                {r.changes.map((c, j) => (
                  <li
                    key={j}
                    className="grid items-start gap-3.5"
                    style={{
                      gridTemplateColumns: "76px 1fr",
                      fontSize: 13.5,
                      lineHeight: 1.5,
                    }}
                  >
                    <TagPill tag={c.tag} />
                    <span>{c.text}</span>
                  </li>
                ))}
              </ul>
            </article>
          ))}

          <div
            className="text-center text-text-muted font-mono border border-dashed border-hair"
            style={{
              padding: "20px 22px",
              fontSize: 11,
              letterSpacing: "0.04em",
            }}
          >
            Pre-0.5.0 history kept internally during closed alpha. Reach out
            if you need it.
          </div>
        </div>
      </div>
    </Page>
  );
}
