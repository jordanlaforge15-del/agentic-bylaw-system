// ABS-342 — the shared ReportDocument template.
//
// Every purchased report type renders through this one chrome, in a fixed
// section order:
//   letterhead (accent rule · logo · report-type · ref)
//   → title block (address + zone)
//   → meta grid (issued · prepared-for · bylaw version · price paid)
//   → determination band (status chip + verdict headline)
//   → summary
//   → body blocks
//   → verification footer
//
// The body is PURE DATA: a typed block array rendered by one shared switch
// (`renderBlock`). No report type carries its own layout code — a new
// report is a new block array, not a new component. Status color/tag is
// centralized in statusInfo() (lib/report.ts).

import { Mono } from "@/components/mono";
import {
  ReportBlock,
  ReportContent,
  formatPrice,
  statusInfo,
} from "@/lib/report";

export function ReportDocument({ report }: { report: ReportContent }) {
  const verdict = statusInfo(report.verdict.status);
  return (
    <article
      data-testid="report-document"
      className="border border-hair bg-surface"
    >
      {/* Accent rule + letterhead. */}
      <div className="h-1 bg-accent" />
      <div className="px-6 sm:px-9 pt-6 flex items-center justify-between gap-3 flex-wrap">
        <div className="flex items-center gap-2.5">
          <span
            className="font-sans font-extrabold text-[17px]"
            style={{ letterSpacing: "-0.03em" }}
          >
            ABS°
          </span>
          <span className="text-text-muted">·</span>
          <Mono muted size={11} data-testid="report-type">
            {report.report_type}
          </Mono>
        </div>
        <Mono muted size={11} data-testid="report-ref">
          {report.ref}
        </Mono>
      </div>

      <div className="px-6 sm:px-9 pb-8 pt-5 flex flex-col gap-6">
        {/* Title block — address + zone. */}
        <header className="flex flex-col gap-1.5 pb-5 border-b border-hair">
          <h1
            className="font-sans font-extrabold m-0 text-[24px] sm:text-[30px] leading-[1.05]"
            style={{ letterSpacing: "-0.03em" }}
            data-testid="report-address"
          >
            {report.address || report.report_type}
          </h1>
          <div className="text-text-muted text-[13.5px]" data-testid="report-zone">
            {report.zone_subtitle}
          </div>
        </header>

        {/* Meta grid — issued · prepared-for · bylaw version · price paid. */}
        <dl
          className="grid grid-cols-2 sm:grid-cols-4 gap-y-3 gap-x-4"
          data-testid="report-meta"
        >
          <MetaCell label="ISSUED" value={report.issued || "—"} />
          <MetaCell label="PREPARED FOR" value={report.prepared_for || "—"} />
          <MetaCell label="BYLAW VERSION" value={report.bylaw_version} />
          <MetaCell
            label="PRICE PAID"
            value={formatPrice(report.price_cents, report.currency)}
          />
        </dl>

        {/* Determination band — status chip + verdict headline. */}
        <div
          className={`border-l-2 ${verdict.border} bg-surface-alt px-4 py-3.5 flex items-center gap-3.5`}
          data-testid="report-verdict"
        >
          <span
            className={`inline-flex items-center px-2 py-0.5 font-mono uppercase text-[10px] tracking-[0.14em] ${verdict.chipBg}`}
            data-testid="report-verdict-chip"
          >
            {verdict.tag}
          </span>
          <span className="font-sans font-bold text-[15px] leading-tight">
            {report.verdict.label}
          </span>
        </div>

        {/* Summary. */}
        {report.summary && (
          <p
            className="text-[14.5px] leading-[1.6] m-0"
            data-testid="report-summary"
          >
            {report.summary}
          </p>
        )}

        {/* Body blocks — the typed array, one shared switch. */}
        <div className="flex flex-col gap-6" data-testid="report-blocks">
          {report.blocks.map((block, i) => (
            <BlockView key={i} block={block} />
          ))}
        </div>

        {/* Verification footer — traceability note + signature line, plus
            the rotated "VERIFIED" stamp that sells the deliverable as a
            formal, verifiable artifact (ABS-368). */}
        <footer
          className="border-t border-hair pt-5 flex flex-col-reverse sm:flex-row sm:items-start gap-4"
          data-testid="report-footer"
        >
          <div className="flex-1 flex flex-col gap-2">
            <p
              className="text-text-muted text-[12px] leading-[1.55] m-0"
              data-testid="report-footer-note"
            >
              {report.footer}
            </p>
            <Mono muted size={10} data-testid="report-signature">
              ABS AGENT · AUTOMATED
            </Mono>
          </div>
          <div
            className="shrink-0 self-center sm:self-start border-2 border-text px-3 py-2 text-center"
            style={{ transform: "rotate(-6deg)" }}
            data-testid="report-verified-stamp"
          >
            <div
              className="font-mono font-bold uppercase text-[11px] tracking-[0.18em]"
            >
              Verified
            </div>
            <div className="font-mono uppercase text-[9px] tracking-[0.14em]">
              ABS · {stampYear(report.issued)}
            </div>
          </div>
        </footer>
      </div>
    </article>
  );
}

// The stamp's year tracks the report's own issued date (not "today") so a
// reprinted or archived report always shows the year it was actually issued.
function stampYear(issued: string): string {
  const year = issued.slice(0, 4);
  return /^\d{4}$/.test(year) ? year : new Date().getFullYear().toString();
}

function MetaCell({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex flex-col gap-1">
      <Mono muted size={9}>
        {label}
      </Mono>
      <span className="text-[13px]">{value}</span>
    </div>
  );
}

// --- The shared block switch ----------------------------------------------

function BlockView({ block }: { block: ReportBlock }) {
  switch (block.type) {
    case "keyvals":
      return <KeyvalsView block={block} />;
    case "uses":
      return <UsesView block={block} />;
    case "finding":
      return <FindingView block={block} />;
    case "table":
      return <TableView block={block} />;
    case "flags":
      return <FlagsView block={block} />;
    case "prose":
      return <ProseView block={block} />;
    default:
      return null;
  }
}

function BlockTitle({ title }: { title?: string }) {
  if (!title) return null;
  return (
    <div className="mb-2.5">
      <Mono muted size={11}>
        {title}
      </Mono>
    </div>
  );
}

function KeyvalsView({ block }: { block: Extract<ReportBlock, { type: "keyvals" }> }) {
  return (
    <section data-testid="block-keyvals">
      <BlockTitle title={block.title} />
      <dl className="grid grid-cols-1 sm:grid-cols-2 gap-x-6 gap-y-2.5">
        {block.items.map((item, i) => (
          <div key={i} className="flex flex-col gap-0.5">
            <dt className="text-text-muted text-[12px]">{item.k}</dt>
            <dd className="m-0 text-[14px]">{item.v}</dd>
          </div>
        ))}
      </dl>
    </section>
  );
}

function UsesView({ block }: { block: Extract<ReportBlock, { type: "uses" }> }) {
  return (
    <section data-testid="block-uses">
      <BlockTitle title={block.title} />
      <ul className="flex flex-col gap-2 m-0 p-0 list-none">
        {block.items.map((item, i) => {
          const info = statusInfo(useStatusToken(item.status));
          return (
            <li
              key={i}
              className="flex items-center justify-between gap-3 border-b border-hair pb-2"
            >
              <span className="text-[14px]">
                {item.use}
                {item.note ? (
                  <span className="text-text-muted"> — {item.note}</span>
                ) : null}
              </span>
              <span
                className={`font-mono uppercase text-[10px] tracking-[0.14em] whitespace-nowrap ${info.fg}`}
              >
                {item.status}
              </span>
            </li>
          );
        })}
      </ul>
    </section>
  );
}

// A `uses` item's status is a permitted/conditional/prohibited *label*;
// map it onto a determination status for coloring.
function useStatusToken(label: string): string {
  const l = label.toLowerCase();
  if (l.includes("prohibit") || l.includes("not permit")) return "fail";
  if (l.includes("condition")) return "conditional";
  if (l.includes("permit")) return "pass";
  return "attention";
}

function FindingView({ block }: { block: Extract<ReportBlock, { type: "finding" }> }) {
  const info = statusInfo(block.status);
  return (
    <section
      data-testid="block-finding"
      className={`border-l-2 ${info.border} bg-surface-alt px-4 py-3`}
    >
      <div className="flex items-center gap-2.5 mb-1.5">
        {block.status && (
          <span
            className={`font-mono uppercase text-[10px] tracking-[0.14em] ${info.fg}`}
          >
            {info.tag}
          </span>
        )}
        {block.title && (
          <span className="font-sans font-bold text-[14px]">{block.title}</span>
        )}
      </div>
      <p className="m-0 text-[13.5px] leading-[1.55] text-text-muted">
        {block.body}
      </p>
    </section>
  );
}

function TableView({ block }: { block: Extract<ReportBlock, { type: "table" }> }) {
  const showStatus = block.rows.some((r) => r.status);
  return (
    <section data-testid="block-table">
      <BlockTitle title={block.title} />
      <div className="overflow-x-auto">
        <table className="w-full border-collapse text-[13px]">
          <thead>
            <tr className="border-b border-hair">
              {block.columns.map((c, i) => (
                <th
                  key={i}
                  className="text-left font-mono uppercase text-[9px] tracking-[0.14em] text-text-muted py-2 pr-4"
                >
                  {c}
                </th>
              ))}
              {showStatus && <th className="py-2" />}
            </tr>
          </thead>
          <tbody>
            {block.rows.map((row, i) => {
              const info = statusInfo(row.status);
              return (
                <tr
                  key={i}
                  className="border-b border-hair last:border-b-0"
                  data-testid="table-row"
                >
                  {row.cells.map((cell, j) => (
                    <td key={j} className="py-2 pr-4 align-top">
                      {cell}
                    </td>
                  ))}
                  {showStatus && (
                    <td className="py-2 align-top text-right">
                      {row.status && (
                        <span
                          className={`font-mono uppercase text-[10px] tracking-[0.14em] ${info.fg}`}
                          data-testid="row-status"
                        >
                          {info.tag}
                        </span>
                      )}
                    </td>
                  )}
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </section>
  );
}

function FlagsView({ block }: { block: Extract<ReportBlock, { type: "flags" }> }) {
  return (
    <section data-testid="block-flags">
      <BlockTitle title={block.title} />
      <ul className="flex flex-col gap-2 m-0 p-0 list-none">
        {block.items.map((item, i) => {
          const info = statusInfo(String(item.status));
          return (
            <li key={i} className="flex items-start gap-2.5">
              <span
                className={`mt-[3px] inline-block w-2 h-2 shrink-0 ${
                  info.favourable ? "bg-accent" : "bg-brick"
                }`}
              />
              <span className="text-[13.5px] leading-[1.5]">{item.text}</span>
            </li>
          );
        })}
      </ul>
    </section>
  );
}

function ProseView({ block }: { block: Extract<ReportBlock, { type: "prose" }> }) {
  return (
    <section data-testid="block-prose">
      <BlockTitle title={block.title} />
      <div className="text-[14px] leading-[1.6] whitespace-pre-line">
        {block.text}
      </div>
    </section>
  );
}
