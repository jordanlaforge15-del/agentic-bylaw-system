// Right pane of /app. Parcel context: address block, inline site-plan SVG
// (intentionally schematic, not survey-accurate), six dotted-hairline
// metadata rows, and a "cited this thread" list. A sticky bottom row
// holds the export + share CTAs.

import { Btn } from "@/components/btn";
import { Mono } from "@/components/mono";

const META: Array<[string, string]> = [
  ["Lot area", "372 m²"],
  ["Frontage", "11.4 m"],
  ["Zoning", "ER-1"],
  ["Existing units", "1 (1924)"],
  ["Heritage", "No"],
  ["Within transit zone", "Yes"],
];

const CITED: Array<{ c: string; n: string; d: string }> = [
  { c: "§ 9.4", n: "Backyard Suites", d: "2025-11-04" },
  { c: "§ 5.4", n: "Yard Requirements", d: "2025-11-04" },
  { c: "§ 2.8", n: "Variances", d: "2025-11-04" },
];

export function ParcelPane() {
  return (
    <aside
      className="border-l border-hair bg-surface-alt flex flex-col min-h-0 overflow-auto"
      style={{ width: 340 }}
    >
      <div className="border-b border-hair px-5 py-4 flex justify-between items-center">
        <Mono muted>PARCEL</Mono>
        <button
          type="button"
          className="bg-transparent border border-hair text-text font-mono cursor-pointer"
          style={{
            padding: "4px 8px",
            fontSize: 9.5,
            letterSpacing: "0.1em",
          }}
        >
          CHANGE
        </button>
      </div>

      <div className="px-5 py-[18px] flex flex-col gap-1.5">
        <div
          className="font-sans font-bold leading-[1.15]"
          style={{ fontSize: 22, letterSpacing: "-0.025em" }}
        >
          5184 Morris St
        </div>
        <div className="text-[12.5px] text-text-muted">
          Halifax, NS · B3J 1B5
        </div>
      </div>

      <div className="px-5 pb-[18px]">
        <div
          className="bg-surface border border-hair relative overflow-hidden"
          style={{ aspectRatio: "4 / 3" }}
        >
          <svg
            viewBox="0 0 200 150"
            className="w-full h-full"
            preserveAspectRatio="xMidYMid meet"
          >
            <rect
              x="20"
              y="20"
              width="160"
              height="110"
              fill="none"
              stroke="var(--text)"
              strokeWidth="0.6"
            />
            <rect
              x="36"
              y="36"
              width="128"
              height="78"
              fill="var(--text)"
              fillOpacity="0.04"
              stroke="var(--text)"
              strokeWidth="0.4"
              strokeDasharray="2 2"
            />
            <rect
              x="56"
              y="48"
              width="60"
              height="40"
              fill="var(--text)"
              fillOpacity="0.08"
              stroke="var(--text)"
              strokeWidth="0.6"
            />
            <rect
              x="120"
              y="68"
              width="34"
              height="34"
              fill="var(--accent)"
              fillOpacity="0.5"
              stroke="var(--accent)"
              strokeWidth="0.8"
            />
            <text
              x="137"
              y="88"
              fontSize="5"
              fill="var(--text)"
              fontFamily="var(--font-mono)"
              textAnchor="middle"
            >
              SUITE
            </text>
            <line
              x1="20"
              y1="14"
              x2="180"
              y2="14"
              stroke="var(--accent)"
              strokeWidth="0.6"
            />
            <text
              x="100"
              y="11"
              fontSize="4.5"
              fill="var(--text)"
              fontFamily="var(--font-mono)"
              textAnchor="middle"
            >
              11.4 m
            </text>
          </svg>
          <div
            className="absolute bottom-1.5 right-2 font-mono text-text-muted"
            style={{ fontSize: 8.5, letterSpacing: "0.14em" }}
          >
            SITE · 1:200
          </div>
        </div>
      </div>

      <div className="px-5 pb-[18px] flex flex-col gap-2">
        {META.map(([k, v]) => (
          <div
            key={k}
            className="flex justify-between text-[12px] pb-1.5"
            style={{ borderBottom: "1px dotted var(--hair)" }}
          >
            <span
              className="text-text-muted font-mono"
              style={{ letterSpacing: "0.04em" }}
            >
              {k}
            </span>
            <span className="font-semibold">{v}</span>
          </div>
        ))}
      </div>

      <div className="border-t border-hair px-5 py-3 flex flex-col gap-2.5">
        <Mono muted>CITED THIS THREAD · 3</Mono>
        {CITED.map((s) => (
          <div
            key={s.c}
            className="bg-surface border border-hair p-3 flex flex-col gap-1"
          >
            <div className="flex justify-between items-baseline">
              <Mono accent size={11} className="font-semibold">
                {s.c}
              </Mono>
              <Mono muted size={9}>
                {s.d}
              </Mono>
            </div>
            <span className="text-[12.5px]">{s.n}</span>
          </div>
        ))}
      </div>

      <div className="mt-auto border-t border-hair px-5 py-3.5 flex flex-col gap-2">
        <Btn variant="primary" size="sm" className="w-full">
          Export reading (PDF)
        </Btn>
        <Btn variant="ghost" size="sm" className="w-full">
          Share with team
        </Btn>
      </div>
    </aside>
  );
}
