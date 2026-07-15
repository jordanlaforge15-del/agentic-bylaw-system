// ABS-342 — structured report deliverable types + status centralization.
//
// A purchased report renders through one shared `ReportDocument` chrome
// (see components/product/report-document.tsx) driven entirely by this
// typed schema. Mirrors advisor.billing.router.ReportContent /
// advisor.billing.report.build_report. The body is a typed BLOCK ARRAY —
// a closed set of block shapes rendered by one shared switch, so no report
// type carries its own layout.

// A determination status. `pass`, `exceeds` and `not_required` are the
// favourable ("accent") verdicts; everything adverse renders in brick. Per-row
// table cells reuse this plus `exceeds` (a favourable over-compliance).
// `not_required` (ABS-377) is a variance-package outcome: the resolved
// requirement is already met, so no variance is required — a distinct
// determination from `pass` ("supportable on the merits").
export type ReportStatus =
  | "pass"
  | "fail"
  | "conditional"
  | "attention"
  | "exceeds"
  | "not_required";

// --- Body blocks -----------------------------------------------------------

export type KeyvalsBlock = {
  type: "keyvals";
  title?: string;
  items: { k: string; v: string }[];
};

export type UsesBlock = {
  type: "uses";
  title?: string;
  items: { use: string; status: string; note?: string | null }[];
};

export type FindingBlock = {
  type: "finding";
  title?: string;
  status?: ReportStatus;
  body: string;
};

export type TableRow = { cells: string[]; status?: ReportStatus | null };

export type TableBlock = {
  type: "table";
  title?: string;
  columns: string[];
  rows: TableRow[];
};

export type FlagsBlock = {
  type: "flags";
  title?: string;
  items: { status: ReportStatus | string; text: string }[];
};

export type ProseBlock = {
  type: "prose";
  title?: string;
  text: string;
};

export type ReportBlock =
  | KeyvalsBlock
  | UsesBlock
  | FindingBlock
  | TableBlock
  | FlagsBlock
  | ProseBlock;

// --- Report envelope -------------------------------------------------------

export type ReportVerdict = { status: string; label: string };

export type ReportContent = {
  ref: string;
  report_type: string;
  address: string;
  zone_subtitle: string;
  issued: string;
  prepared_for: string;
  bylaw_version: string;
  price_cents: number;
  currency: string;
  verdict: ReportVerdict;
  summary: string;
  blocks: ReportBlock[];
  footer: string;
};

// --- Status → color/tag (centralized) --------------------------------------

// The single source of truth for how a determination status paints. `pass`
// and `exceeds` are favourable (accent); every adverse/neutral status is
// brick. `tag` is the short uppercase chip label. Returns Tailwind token
// class names bound to the design theme (globals.css).
export type StatusInfo = {
  tag: string;
  // Foreground text class for the chip/label.
  fg: string;
  // Background class for a filled chip.
  chipBg: string;
  // Border class for outlined treatments (e.g. the determination band rule).
  border: string;
  favourable: boolean;
};

export function statusInfo(status: string | null | undefined): StatusInfo {
  switch (status) {
    case "pass":
      return {
        tag: "PASS",
        fg: "text-accent-ink",
        chipBg: "bg-accent text-on-accent",
        border: "border-accent",
        favourable: true,
      };
    case "exceeds":
      return {
        tag: "EXCEEDS",
        fg: "text-accent-ink",
        chipBg: "bg-accent text-on-accent",
        border: "border-accent",
        favourable: true,
      };
    case "not_required":
      // ABS-377 — a variance package whose resolved requirement is already
      // met. Favourable (no variance needed), but the tag says NOT REQUIRED,
      // not PASS, so it never misreads as "the variance is supportable".
      return {
        tag: "NOT REQUIRED",
        fg: "text-accent-ink",
        chipBg: "bg-accent text-on-accent",
        border: "border-accent",
        favourable: true,
      };
    case "fail":
      return {
        tag: "FAIL",
        fg: "text-brick",
        chipBg: "bg-brick text-white",
        border: "border-brick",
        favourable: false,
      };
    case "conditional":
      return {
        tag: "CONDITIONAL",
        fg: "text-brick",
        chipBg: "bg-brick text-white",
        border: "border-brick",
        favourable: false,
      };
    default:
      // attention / unknown — adverse-neutral, brick palette so it never
      // misreads as a green all-clear.
      return {
        tag: "ATTENTION",
        fg: "text-brick",
        chipBg: "bg-brick text-white",
        border: "border-brick",
        favourable: false,
      };
  }
}

// Format cents → "$199.00 CAD" for the meta grid's price-paid cell.
export function formatPrice(cents: number, currency: string): string {
  const amount = (cents / 100).toLocaleString("en-CA", {
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  });
  return `$${amount} ${currency}`;
}
