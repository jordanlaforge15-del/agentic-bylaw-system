// Unified billing content shared by `/billing` (marketing chrome) and
// `/app/billing` (AuthShell) — ABS-388. One component so the two routes
// render identical account content inside their respective shells; only
// the sign-in destination differs.
//
// The beta pivot bills chat by an account-level token wallet surfaced as
// "~N turns" (never raw token counts, never tier/credit/pack vocabulary).
// This view stacks four cards:
//
//   * BalanceCard      — "~N turns" headline from approx_turns_remaining,
//     a LOW badge when low_balance; inline top-up actions (labeled in
//     turns) when payments are on, or the free-trial beta banner when off.
//   * TransactionsCard — the wallet ledger (grants / top-ups / usage) from
//     GET /api/billing/wallet/transactions, each delta shown as turns.
//   * ReportsCard      — owned report purchases (incl. now-disabled slugs),
//     each linking into the unified workspace at /app?report_id=.
//   * CasesCard        — recent cases (no tier column).
//
// Client-fetched from the /api proxies on mount (consistent with the
// sidebar / balance-strip pattern); a 401 on the wallet fetch drives the
// signed-out UnauthorizedCard, preserving the prior behaviour on both
// routes. Attention states follow the design package's global rules: the
// BRICK colour + an alarm glyph for LOW (never colour alone), the `~`
// prefix + the standard "approximate" disclosure, mono kickers.

"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { Btn } from "@/components/btn";
import { Mono } from "@/components/mono";
import {
  BetaRefillClaim,
  canClaimRefill,
  refillTurnsLabel,
  refillUnlockLabel,
} from "@/components/product/beta-refill";
import {
  CaseListResponse,
  CaseRow,
  TopupCatalogResponse,
  WalletResponse,
  WalletTransaction,
  WalletTransactionsResponse,
  formatCurrency,
} from "@/lib/cases";
import { TURN_APPROX_SHORT } from "@/lib/turn-copy";

// Owned report row (GET /api/billing/questions/purchases). Mirrors the
// backend ReportSummary the sidebar consumes; the list is deliberately
// NOT gated by slug enablement, so a report bought against a now-disabled
// question still appears and stays openable.
type ReportRow = {
  id: number;
  question_slug: string;
  title: string;
  status: string;
  address?: string | null;
  zone?: string | null;
  answer_ready?: boolean;
  updated_at?: string | null;
};

const APPROX_DISCLOSURE = TURN_APPROX_SHORT;

type FetchResult<T> =
  | { ok: true; data: T }
  | { ok: false; status: number };

async function getJson<T>(url: string): Promise<FetchResult<T>> {
  try {
    const r = await fetch(url, { cache: "no-store" });
    if (!r.ok) return { ok: false, status: r.status };
    return { ok: true, data: (await r.json()) as T };
  } catch {
    return { ok: false, status: 0 };
  }
}

// Turns are backend-owned for the *balance* headline (rendered straight
// off approx_turns_remaining). A historical ledger delta has no backend
// turns figure, so we approximate it from tokens_per_turn purely for
// display — always `~`-prefixed and never shown as a raw token count, in
// keeping with the turns-only copy rule. Sub-turn magnitudes collapse to
// "<1 turn" rather than a misleading "~0".
function turnsDelta(tokens: number, perTurn: number): string {
  const sign = tokens >= 0 ? "+" : "−";
  if (!perTurn) return "";
  const mag = Math.abs(tokens) / perTurn;
  if (mag === 0) return "0 turns";
  if (mag < 1) return `${sign}<1 turn`;
  const n = Math.round(mag);
  return `${sign}~${n} ${n === 1 ? "turn" : "turns"}`;
}

const ENTRY_LABEL: Record<WalletTransaction["entry_type"], string> = {
  grant: "Grant",
  topup: "Top-up",
  burn: "Usage",
  adjust: "Adjustment",
};

function formatDate(iso: string | null | undefined): string {
  if (!iso) return "—";
  const t = Date.parse(iso);
  if (Number.isNaN(t)) return "—";
  return new Date(t).toLocaleDateString("en-CA", {
    year: "numeric",
    month: "short",
    day: "numeric",
  });
}

type Props = {
  /** Where the signed-out card sends the visitor to authenticate. */
  signInHref: string;
};

export function BillingContent({ signInHref }: Props) {
  const [status, setStatus] = useState<
    "loading" | "unauthorized" | "error" | "ready"
  >("loading");
  const [wallet, setWallet] = useState<WalletResponse | null>(null);
  const [transactions, setTransactions] = useState<WalletTransaction[]>([]);
  const [reports, setReports] = useState<ReportRow[]>([]);
  const [cases, setCases] = useState<CaseRow[]>([]);
  const [topups, setTopups] = useState<TopupCatalogResponse | null>(null);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      const [w, tx, rep, cs, tops] = await Promise.all([
        getJson<WalletResponse>("/api/billing/wallet"),
        getJson<WalletTransactionsResponse>(
          "/api/billing/wallet/transactions?limit=25",
        ),
        getJson<{ reports: ReportRow[] }>(
          "/api/billing/questions/purchases",
        ),
        getJson<CaseListResponse>("/api/cases"),
        getJson<TopupCatalogResponse>("/api/billing/topups"),
      ]);
      if (cancelled) return;

      // The wallet fetch is the auth gate: a 401 means signed-out.
      if (!w.ok && w.status === 401) {
        setStatus("unauthorized");
        return;
      }
      if (!w.ok) {
        setStatus("error");
        return;
      }
      setWallet(w.data);
      setTransactions(tx.ok ? tx.data.transactions : []);
      setReports(rep.ok ? rep.data.reports : []);
      setCases(cs.ok ? cs.data.cases : []);
      setTopups(tops.ok ? tops.data : null);
      setStatus("ready");
    })();
    return () => {
      cancelled = true;
    };
  }, []);

  if (status === "loading") {
    return (
      <div
        data-testid="billing-loading"
        className="bg-surface-alt border border-hair p-8 text-[13.5px] text-text-muted"
      >
        Loading your balance…
      </div>
    );
  }

  if (status === "unauthorized") {
    return <UnauthorizedCard signInHref={signInHref} />;
  }

  if (status === "error" || !wallet) {
    return (
      <div className="bg-surface-alt border border-hair p-8">
        <div className="font-semibold mb-2">Couldn&apos;t load your balance</div>
        <div className="text-text-muted text-[13.5px]">
          Please try refreshing the page.
        </div>
      </div>
    );
  }

  return (
    <div className="flex flex-col gap-9 sm:gap-12">
      <BalanceCard wallet={wallet} topups={topups} onRefilled={setWallet} />
      <TransactionsCard
        transactions={transactions}
        tokensPerTurn={wallet.tokens_per_turn}
      />
      <ReportsCard reports={reports} />
      <CasesCard cases={cases} />
    </div>
  );
}

function UnauthorizedCard({ signInHref }: { signInHref: string }) {
  return (
    <div
      data-testid="billing-unauthorized"
      className="bg-surface-alt border border-hair p-8"
    >
      <div className="font-semibold mb-2">Sign in to view your balance</div>
      <div className="text-text-muted text-[13.5px] mb-4">
        Your turn balance and account history live behind your account.
      </div>
      <Link href={signInHref}>
        <Btn variant="primary" size="sm">
          Sign in
        </Btn>
      </Link>
    </div>
  );
}

function BalanceCard({
  wallet,
  topups,
  onRefilled,
}: {
  wallet: WalletResponse;
  onRefilled: (wallet: WalletResponse) => void;
  topups: TopupCatalogResponse | null;
}) {
  const turns = wallet.approx_turns_remaining;
  const turnWord = turns === 1 ? "turn" : "turns";
  const low = wallet.low_balance;
  const paymentsOn = wallet.payments_enabled;
  // ABS-405 — the self-serve claim, offered on the payments-off beta only.
  const refill = canClaimRefill(wallet) ? wallet.beta_refill : undefined;
  const cooldownUntil =
    !paymentsOn && wallet.beta_refill?.status === "cooldown"
      ? refillUnlockLabel(wallet.beta_refill.next_available_at)
      : null;

  return (
    <section data-testid="billing-balance">
      <div className="flex items-baseline justify-between mb-3 sm:mb-4">
        <Mono muted size={11}>
          TURN BALANCE
        </Mono>
        {low && (
          <span
            data-testid="billing-low-badge"
            className="flex items-center gap-1 text-brick font-semibold text-[12px]"
          >
            <span aria-hidden className="font-bold leading-none">
              ▲
            </span>
            LOW
          </span>
        )}
      </div>

      <div className="bg-surface-alt border border-hair p-6 sm:p-8">
        <div className="flex items-baseline gap-2">
          <span
            data-testid="billing-turns"
            className={
              low
                ? "text-brick font-extrabold text-[44px] sm:text-[56px] leading-none"
                : "font-extrabold text-[44px] sm:text-[56px] leading-none"
            }
            style={{ letterSpacing: "-0.04em" }}
            aria-label={`approximately ${turns} ${turnWord} remaining. ${APPROX_DISCLOSURE}.`}
          >
            ~{turns} {turnWord}
          </span>
        </div>
        <div
          data-testid="billing-approx-note"
          className="text-text-muted text-[12.5px] mt-2"
        >
          remaining · {APPROX_DISCLOSURE}
        </div>

        {paymentsOn ? (
          <TopUpActions topups={topups} />
        ) : (
          <div
            data-testid="billing-beta-banner"
            className="border border-hair bg-surface p-4 mt-6 text-[13px] text-text-muted max-w-[520px] flex flex-col gap-3 items-start"
          >
            <div>
              These are free trial turns. Paid top-ups open soon — your balance
              and full history stay saved in the meantime.
              {/* ABS-405: this page is where a stuck user comes looking for a
                  way to pay. While there is nothing to sell them, it is also
                  where the self-serve claim belongs — and where the wait is
                  worth naming, so "open soon" isn't the only answer. */}
              {refill
                ? ` You can add ${refillTurnsLabel(refill)} to this account right now.`
                : cooldownUntil
                  ? ` More turns unlock ${cooldownUntil}.`
                  : ""}
            </div>
            {refill && (
              <BetaRefillClaim
                onRefilled={onRefilled}
                testId="billing-beta-refill-btn"
              />
            )}
          </div>
        )}
      </div>
    </section>
  );
}

// Inline top-up actions (payments-on only). Each option is labeled in
// turns ("Top up ~N turns · $X") — the approx_turns figure is backend-
// owned. Clicking starts a checkout (POST /api/billing/checkout/topup →
// Stripe/MockStripe URL) and redirects; on return the page remounts and
// refetches the wallet.
function TopUpActions({ topups }: { topups: TopupCatalogResponse | null }) {
  const [working, setWorking] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const options = (topups?.options ?? []).filter((o) => o.available);

  async function startTopUp(sku: string) {
    if (working) return;
    setWorking(sku);
    setError(null);
    try {
      const r = await fetch("/api/billing/checkout/topup", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ sku }),
      });
      if (!r.ok) {
        setError("Top-up isn't available right now. Please try again shortly.");
        return;
      }
      const data = (await r.json()) as { url?: string };
      if (data.url && typeof window !== "undefined") {
        window.location.href = data.url;
        return;
      }
      setError("Top-up isn't available right now. Please try again shortly.");
    } catch {
      setError("Top-up isn't available right now. Please try again shortly.");
    } finally {
      setWorking(null);
    }
  }

  if (options.length === 0) {
    return (
      <div className="text-text-muted text-[13px] mt-6">
        Top-ups are momentarily unavailable. Please check back shortly.
      </div>
    );
  }

  return (
    <div className="mt-6" data-testid="billing-topups">
      <Mono muted size={10} className="block mb-3">
        ADD TURNS
      </Mono>
      <div className="flex flex-wrap gap-2.5">
        {options.map((o) => (
          <Btn
            key={o.sku}
            variant="accent"
            size="sm"
            data-testid="billing-topup-btn"
            disabled={working !== null}
            onClick={() => startTopUp(o.sku)}
          >
            {working === o.sku
              ? "Opening checkout…"
              : `Top up ~${o.approx_turns} turns · ${formatCurrency(
                  o.price_cents,
                  topups?.currency ?? "CAD",
                )}`}
          </Btn>
        ))}
      </div>
      {error && (
        <div className="text-[12.5px] text-brick mt-2" data-testid="billing-topup-error">
          {error}
        </div>
      )}
    </div>
  );
}

function TransactionsCard({
  transactions,
  tokensPerTurn,
}: {
  transactions: WalletTransaction[];
  tokensPerTurn: number;
}) {
  return (
    <section data-testid="billing-transactions">
      <div className="mb-3 sm:mb-4">
        <Mono muted size={11}>
          WALLET LEDGER
        </Mono>
        <h2
          className="font-sans font-extrabold m-0 mt-1.5 text-[20px] sm:text-[24px]"
          style={{ letterSpacing: "-0.03em" }}
        >
          Transactions
        </h2>
      </div>
      {transactions.length === 0 ? (
        <div className="bg-surface-alt border border-hair p-5 text-[13px] text-text-muted">
          No transactions yet. Grants, top-ups, and usage will appear here.
        </div>
      ) : (
        <div className="border border-hair divide-y divide-hair">
          {transactions.map((t) => {
            const delta = turnsDelta(t.amount_tokens, tokensPerTurn);
            const positive = t.amount_tokens >= 0;
            return (
              <div
                key={t.id}
                data-testid="billing-tx-row"
                className="p-4 sm:px-5 flex items-center justify-between gap-4"
              >
                <div className="min-w-0">
                  <div className="font-semibold text-[13.5px]">
                    {ENTRY_LABEL[t.entry_type]}
                  </div>
                  <div className="text-[12px] text-text-muted">
                    {formatDate(t.created_at)}
                  </div>
                </div>
                <div
                  className={
                    positive
                      ? "font-mono text-[13px] whitespace-nowrap text-text"
                      : "font-mono text-[13px] whitespace-nowrap text-text-muted"
                  }
                >
                  {delta}
                </div>
              </div>
            );
          })}
        </div>
      )}
    </section>
  );
}

function ReportsCard({ reports }: { reports: ReportRow[] }) {
  return (
    <section data-testid="billing-reports">
      <div className="mb-3 sm:mb-4">
        <Mono muted size={11}>
          REPORTS
        </Mono>
        <h2
          className="font-sans font-extrabold m-0 mt-1.5 text-[20px] sm:text-[24px]"
          style={{ letterSpacing: "-0.03em" }}
        >
          Report history
        </h2>
      </div>
      {reports.length === 0 ? (
        <div className="bg-surface-alt border border-hair p-5 text-[13px] text-text-muted">
          No reports yet. Open one from{" "}
          <Link href="/cases/new" className="underline">
            /cases/new
          </Link>
          .
        </div>
      ) : (
        <div className="border border-hair divide-y divide-hair">
          {reports.map((r) => (
            <Link
              key={r.id}
              href={`/app?report_id=${r.id}`}
              data-testid="billing-report-row"
              className="p-4 sm:px-5 flex items-center justify-between gap-4 hover:bg-surface-alt transition-colors"
            >
              <div className="min-w-0">
                <div className="font-semibold text-[13.5px] truncate max-w-[420px]">
                  {r.title?.trim() || "Report"}
                </div>
                <div className="text-[12px] text-text-muted truncate max-w-[420px]">
                  {r.address?.trim() || formatDate(r.updated_at)}
                </div>
              </div>
              <ReportStatusTag status={r.status} ready={!!r.answer_ready} />
            </Link>
          ))}
        </div>
      )}
    </section>
  );
}

function ReportStatusTag({
  status,
  ready,
}: {
  status: string;
  ready: boolean;
}) {
  if (status === "voided" || status === "failed") {
    return (
      <Mono size={9.5} className="text-brick whitespace-nowrap">
        FAILED
      </Mono>
    );
  }
  if (ready || status === "captured") {
    return (
      <Mono muted size={9.5} className="whitespace-nowrap">
        READY
      </Mono>
    );
  }
  return (
    <Mono muted size={9.5} className="whitespace-nowrap">
      GENERATING
    </Mono>
  );
}

function CasesCard({ cases }: { cases: CaseRow[] }) {
  const recent = cases.slice(0, 8);
  return (
    <section data-testid="billing-cases">
      <div className="flex items-baseline justify-between mb-3 sm:mb-4">
        <div>
          <Mono muted size={11}>
            CASES
          </Mono>
          <h2
            className="font-sans font-extrabold m-0 mt-1.5 text-[20px] sm:text-[24px]"
            style={{ letterSpacing: "-0.03em" }}
          >
            Recent cases
          </h2>
        </div>
        <Link href="/cases" className="text-[12.5px] underline text-text-muted">
          See all
        </Link>
      </div>
      {recent.length === 0 ? (
        <div className="bg-surface-alt border border-hair p-5 text-[13px] text-text-muted">
          No cases yet. Open one from{" "}
          <Link href="/cases/new" className="underline">
            /cases/new
          </Link>
          .
        </div>
      ) : (
        <div className="border border-hair divide-y divide-hair">
          {recent.map((c) => (
            <Link
              key={c.id}
              href={`/app?case_id=${c.id}`}
              data-testid="billing-case-row"
              className="p-4 sm:px-5 flex items-center justify-between gap-4 hover:bg-surface-alt transition-colors"
            >
              <div className="min-w-0">
                <div className="font-semibold text-[13.5px] truncate max-w-[420px]">
                  {c.anchor_label || `Case #${c.user_case_number}`}
                </div>
                <div className="text-[12px] text-text-muted capitalize">
                  {c.status}
                </div>
              </div>
              <div className="text-[12px] text-text-muted whitespace-nowrap">
                {formatDate(c.last_activity_at)}
              </div>
            </Link>
          ))}
        </div>
      )}
    </section>
  );
}
