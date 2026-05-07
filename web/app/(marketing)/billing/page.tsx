// /billing — logged-in account view. Plan card (inverted) + payment
// method, then invoice history table, then usage panels (sparkline +
// top parcels). Lives under the marketing chrome per the spec; only
// /app bypasses it.
//
// The usage sparkline heights are precomputed so the component can
// stay a server component (no Math.random() at render time, which
// would mismatch hydration).

import { Btn } from "@/components/btn";
import { Mono } from "@/components/mono";

type Invoice = {
  id: string;
  date: string;
  plan: string;
  amount: string;
  status: string;
};

const INVOICES: Invoice[] = [
  {
    id: "INV-2026-0421",
    date: "2026-04-30",
    plan: "Practice · 4 seats",
    amount: "$720.00",
    status: "PAID",
  },
  {
    id: "INV-2026-0398",
    date: "2026-03-30",
    plan: "Practice · 4 seats",
    amount: "$720.00",
    status: "PAID",
  },
  {
    id: "INV-2026-0367",
    date: "2026-02-28",
    plan: "Practice · 3 seats",
    amount: "$540.00",
    status: "PAID",
  },
  {
    id: "INV-2026-0341",
    date: "2026-01-30",
    plan: "Practice · 3 seats",
    amount: "$540.00",
    status: "PAID",
  },
  {
    id: "INV-2025-0322",
    date: "2025-12-30",
    plan: "Practice · 2 seats",
    amount: "$360.00",
    status: "PAID",
  },
];

const TOP_PARCELS = [
  { addr: "5184 Morris St", n: 42 },
  { addr: "1208 Robie St", n: 38 },
  { addr: "17 Edward St", n: 24 },
  { addr: "2310 Gottingen St", n: 19 },
];

// Deterministic 30-day usage histogram. Heights chosen to read like
// real usage (mid-week peaks, weekend dips) — last four bars are
// "today and the last few days" highlighted in accent.
const USAGE_HEIGHTS = [
  38, 52, 41, 60, 48, 22, 18, 44, 58, 51, 66, 55, 30, 24, 47, 62, 70, 58, 49,
  33, 27, 51, 64, 59, 71, 55, 36, 68, 82, 90,
];

const ROW_GRID = "1.5fr 1fr 2fr 1fr 1fr 0.5fr";

export default function BillingPage() {
  return (
    <div
      className="px-8 py-14 mx-auto"
      style={{ maxWidth: 1200, minHeight: "calc(100vh - 280px)" }}
    >
      <header className="flex flex-col gap-3.5 pb-7 mb-9 border-b border-hair">
        <Mono muted size={11}>
          ACCOUNT · BILLING
        </Mono>
        <h1
          className="font-sans font-extrabold m-0"
          style={{ fontSize: 56, letterSpacing: "-0.04em", lineHeight: 0.98 }}
        >
          Billing.
        </h1>
        <p
          className="text-[17px] text-text-muted leading-[1.45] m-0"
          style={{ maxWidth: 620 }}
        >
          Halifax Studio Co. · Practice plan · 4 seats. Invoices below; export
          anytime.
        </p>
      </header>

      <div
        className="grid mb-9"
        style={{ gridTemplateColumns: "2fr 1fr", gap: 14 }}
      >
        <PlanCard />
        <PaymentMethod />
      </div>

      <div className="flex justify-between items-center mb-3.5">
        <Mono muted size={11}>
          INVOICE HISTORY
        </Mono>
        <Btn variant="quiet" size="sm">
          Export all (.csv)
        </Btn>
      </div>
      <div className="border border-hair">
        <div
          className="grid bg-surface-alt border-b border-hair"
          style={{
            gridTemplateColumns: ROW_GRID,
            gap: 16,
            padding: "12px 18px",
          }}
        >
          {["INVOICE", "DATE", "DESCRIPTION", "AMOUNT", "STATUS", ""].map(
            (h, i) => (
              <Mono muted size={9.5} key={i}>
                {h}
              </Mono>
            ),
          )}
        </div>
        {INVOICES.map((inv, i) => (
          <div
            key={inv.id}
            className="grid items-center text-[13px]"
            style={{
              gridTemplateColumns: ROW_GRID,
              gap: 16,
              padding: "14px 18px",
              borderBottom:
                i < INVOICES.length - 1 ? "1px solid var(--hair)" : "none",
            }}
          >
            <span className="font-mono text-[12px]">{inv.id}</span>
            <span className="text-text-muted">{inv.date}</span>
            <span>{inv.plan}</span>
            <span
              className="font-semibold"
              style={{ letterSpacing: "-0.01em" }}
            >
              {inv.amount}
            </span>
            <span>
              <Mono accent size={9.5}>
                {inv.status}
              </Mono>
            </span>
            <button
              className="bg-transparent border-none text-text-muted cursor-pointer font-mono text-[10px] text-right hover:text-text"
              style={{ letterSpacing: "0.08em" }}
            >
              PDF ↓
            </button>
          </div>
        ))}
      </div>

      <div
        className="mt-9 grid"
        style={{ gridTemplateColumns: "1fr 1fr", gap: 14 }}
      >
        <UsageCard />
        <TopParcelsCard />
      </div>
    </div>
  );
}

function PlanCard() {
  return (
    <div
      className="flex flex-col gap-[18px]"
      style={{
        background: "var(--text)",
        color: "var(--surface)",
        padding: 28,
      }}
    >
      <div className="flex justify-between items-start">
        <div>
          <Mono size={10} style={{ color: "rgba(255,255,255,0.6)" }}>
            CURRENT PLAN
          </Mono>
          <div
            className="font-sans font-extrabold mt-1.5"
            style={{ fontSize: 36, letterSpacing: "-0.035em" }}
          >
            Practice
          </div>
          <div
            className="text-[13px] mt-1"
            style={{ color: "rgba(255,255,255,0.7)" }}
          >
            $180 / seat / month · billed monthly
          </div>
        </div>
        <span
          className="font-mono"
          style={{
            background: "var(--accent)",
            color: "var(--on-accent)",
            padding: "4px 10px",
            fontSize: 9.5,
            letterSpacing: "0.14em",
          }}
        >
          ACTIVE
        </span>
      </div>

      <div
        className="grid pt-[18px]"
        style={{
          gridTemplateColumns: "repeat(3, 1fr)",
          gap: 24,
          borderTop: "1px solid rgba(255,255,255,0.15)",
        }}
      >
        {[
          { l: "SEATS", v: "4 / 10" },
          { l: "READINGS · MAY", v: "247" },
          { l: "NEXT INVOICE", v: "May 30" },
        ].map((s) => (
          <div key={s.l}>
            <Mono size={9.5} style={{ color: "rgba(255,255,255,0.55)" }}>
              {s.l}
            </Mono>
            <div
              className="font-sans font-bold mt-1"
              style={{ fontSize: 26, letterSpacing: "-0.025em" }}
            >
              {s.v}
            </div>
          </div>
        ))}
      </div>

      <div className="flex gap-2.5 mt-1.5">
        <Btn variant="accent" size="sm">
          Manage seats
        </Btn>
        <Btn
          variant="ghost"
          size="sm"
          style={{
            borderColor: "rgba(255,255,255,0.3)",
            color: "var(--surface)",
          }}
        >
          Change plan
        </Btn>
      </div>
    </div>
  );
}

function PaymentMethod() {
  return (
    <div
      className="bg-surface-alt border border-hair flex flex-col gap-3.5"
      style={{ padding: 24 }}
    >
      <Mono muted size={10}>
        PAYMENT METHOD
      </Mono>
      <div className="flex items-center gap-3">
        <div
          className="bg-text text-surface flex items-center justify-center font-mono"
          style={{
            width: 44,
            height: 30,
            fontSize: 9,
            letterSpacing: "0.06em",
          }}
        >
          VISA
        </div>
        <div className="flex flex-col">
          <span className="text-[14px] font-semibold">
            •••• •••• •••• 4421
          </span>
          <span className="text-[12px] text-text-muted">Expires 11/28</span>
        </div>
      </div>
      <Btn variant="ghost" size="sm">
        Update card
      </Btn>
      <div className="pt-3.5 border-t border-hair flex flex-col gap-1.5">
        <Mono muted size={10}>
          BILLING EMAIL
        </Mono>
        <span className="text-[13px]">billing@halifaxstudio.co</span>
      </div>
    </div>
  );
}

function UsageCard() {
  const max = Math.max(...USAGE_HEIGHTS);
  return (
    <div
      className="bg-surface-alt border border-hair"
      style={{ padding: 24 }}
    >
      <Mono muted size={10}>
        USAGE · MAY 2026
      </Mono>
      <div className="flex items-baseline gap-2 mt-2 mb-3.5">
        <span
          className="font-sans font-extrabold"
          style={{ fontSize: 38, letterSpacing: "-0.035em" }}
        >
          247
        </span>
        <span className="text-[13px] text-text-muted">
          readings · unlimited
        </span>
      </div>
      <div className="flex items-end gap-[2px] h-8">
        {USAGE_HEIGHTS.map((h, i) => {
          const recent = i >= USAGE_HEIGHTS.length - 4;
          return (
            <div
              key={i}
              className="flex-1"
              style={{
                background: recent ? "var(--accent)" : "var(--text)",
                opacity: recent ? 1 : 0.3,
                height: `${(h / max) * 100}%`,
              }}
            />
          );
        })}
      </div>
      <div className="flex justify-between mt-2">
        <Mono muted size={9}>
          MAY 1
        </Mono>
        <Mono muted size={9}>
          MAY 30
        </Mono>
      </div>
    </div>
  );
}

function TopParcelsCard() {
  return (
    <div
      className="bg-surface-alt border border-hair flex flex-col gap-3"
      style={{ padding: 24 }}
    >
      <Mono muted size={10}>
        TOP PARCELS · THIS MONTH
      </Mono>
      {TOP_PARCELS.map((p) => (
        <div
          key={p.addr}
          className="flex justify-between items-center pb-2 border-b border-hair"
        >
          <span className="text-[13.5px]">{p.addr}</span>
          <span className="font-mono text-[12px] text-text-muted">
            {p.n} readings
          </span>
        </div>
      ))}
    </div>
  );
}
