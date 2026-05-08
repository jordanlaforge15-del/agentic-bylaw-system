// Right pane of /app. Shows the parcel context derived from the
// current session's spatial-join tool results: address, geocode
// confidence, zone / height / heritage / FAR / bonus / shadow rows
// (only the ones actually returned by the spatial query — empty
// datasets are dropped), and a "cited this thread" list of distinct
// citations the agent has pulled.
//
// When `parcel` is null, we render an honest empty state rather than
// stale fixtures. The site-plan SVG stays as a schematic placeholder
// for now; eventually it would be drawn from the resolved parcel
// polygon, but that needs the geocoder to surface the parcel
// geometry first.

import { Btn } from "@/components/btn";
import { Mono } from "@/components/mono";
import type { ParcelContext } from "@/lib/parcel";

type Props = {
  parcel: ParcelContext | null;
};

export function ParcelPane({ parcel }: Props) {
  return (
    <aside
      className="border-l border-hair bg-surface-alt flex flex-col min-h-0 overflow-auto"
      style={{ width: 340 }}
    >
      <div className="border-b border-hair px-5 py-4 flex justify-between items-center">
        <Mono muted>PARCEL</Mono>
        {parcel && (
          <Mono muted size={9.5}>
            {parcel.geocode
              ? `${parcel.geocode.resolver?.toUpperCase() || "GEOCODED"} · ${(parcel.geocode.confidence * 100).toFixed(0)}%`
              : "—"}
          </Mono>
        )}
      </div>

      {parcel ? <ParcelDetails parcel={parcel} /> : <EmptyParcel />}

      <div className="mt-auto border-t border-hair px-5 py-3.5 flex flex-col gap-2">
        <Btn
          variant="primary"
          size="sm"
          className="w-full"
          disabled={!parcel}
          style={{ opacity: parcel ? 1 : 0.5 }}
        >
          Export reading (PDF)
        </Btn>
        <Btn
          variant="ghost"
          size="sm"
          className="w-full"
          disabled={!parcel}
          style={{ opacity: parcel ? 1 : 0.5 }}
        >
          Share with team
        </Btn>
      </div>
    </aside>
  );
}

function EmptyParcel() {
  return (
    <div className="px-5 py-7 flex flex-col gap-3">
      <div
        className="font-sans font-bold leading-[1.15]"
        style={{ fontSize: 18, letterSpacing: "-0.02em" }}
      >
        No parcel yet.
      </div>
      <p className="text-[13px] text-text-muted leading-[1.55] m-0">
        Ask a question with a Halifax civic address — e.g.{" "}
        <em>&ldquo;What zone is 1967 Woodlawn Terrace?&rdquo;</em> — and the
        spatial-join attributes will land here: zone, max height, heritage
        district, FAR, bonus zoning, shadow-impact overlap.
      </p>
    </div>
  );
}

function ParcelDetails({ parcel }: { parcel: ParcelContext }) {
  const rows = buildRows(parcel);
  return (
    <>
      <div className="px-5 py-[18px] flex flex-col gap-1.5">
        <div
          className="font-sans font-bold leading-[1.15]"
          style={{ fontSize: 22, letterSpacing: "-0.025em" }}
        >
          {parcel.address.civic_number} {parcel.address.street}
        </div>
        <div className="text-[12.5px] text-text-muted">
          Halifax Regional Municipality
        </div>
      </div>

      <div className="px-5 pb-[18px] flex flex-col gap-2">
        {rows.map(([k, v]) => (
          <div
            key={k}
            className="flex justify-between gap-3 text-[12px] pb-1.5"
            style={{ borderBottom: "1px dotted var(--hair)" }}
          >
            <span
              className="text-text-muted font-mono shrink-0"
              style={{ letterSpacing: "0.04em" }}
            >
              {k}
            </span>
            <span
              className="font-semibold text-right"
              style={{ wordBreak: "break-word" }}
            >
              {v}
            </span>
          </div>
        ))}
      </div>

      {parcel.cited.length > 0 && (
        <div className="border-t border-hair px-5 py-3 flex flex-col gap-2.5">
          <Mono muted>CITED THIS THREAD · {parcel.cited.length}</Mono>
          {parcel.cited.map((s) => (
            <div
              key={s.citation}
              className="bg-surface border border-hair p-3 flex flex-col gap-1"
            >
              <div className="flex justify-between items-baseline gap-2">
                <Mono accent size={11} className="font-semibold">
                  {compactCitation(s.citation)}
                </Mono>
                {s.date && (
                  <Mono muted size={9}>
                    {s.date}
                  </Mono>
                )}
              </div>
              <span className="text-[12.5px] text-text-muted">{s.title}</span>
            </div>
          ))}
        </div>
      )}
    </>
  );
}

function buildRows(parcel: ParcelContext): Array<[string, string]> {
  const rows: Array<[string, string]> = [];
  if (parcel.zone) {
    rows.push([
      "Zone",
      parcel.zone.description
        ? `${parcel.zone.code} · ${parcel.zone.description}`
        : parcel.zone.code,
    ]);
  }
  if (parcel.height && parcel.height.max_m != null) {
    rows.push(["Max height", `${parcel.height.max_m} m`]);
  }
  if (parcel.heritage) {
    rows.push([
      "Heritage",
      parcel.heritage.status
        ? `${parcel.heritage.name} (${parcel.heritage.status})`
        : parcel.heritage.name,
    ]);
  }
  if (parcel.far && parcel.far.max != null) {
    rows.push(["Max FAR", parcel.far.max.toString()]);
  }
  if (parcel.bonus) {
    rows.push(["Bonus zoning", parcel.bonus.name]);
  }
  if (parcel.shadow) {
    rows.push(["Shadow impact", parcel.shadow.area]);
  }
  if (rows.length === 0) {
    rows.push(["Spatial match", "Address geocoded but no attribute layers hit"]);
  }
  return rows;
}

// "Schedule 17 > 117 > [Maximum Streetwall Heights] > (a)" → "§ 117(a)"-ish.
// The full path is too noisy for a card; we keep the most distinctive
// segment (the leaf label) plus an optional schedule prefix.
function compactCitation(path: string): string {
  const parts = path.split(/\s*>\s*/);
  if (parts.length === 1) return path;
  const lead = parts[0];
  const tail = parts[parts.length - 1];
  if (lead.toLowerCase().startsWith("schedule")) {
    return `${lead} · ${tail}`;
  }
  return tail;
}
