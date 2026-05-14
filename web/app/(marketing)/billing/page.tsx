// /billing — logged-in account view. Replaces the v1 subscription
// page with a credit-balance summary + purchase history + recent
// cases. Lives under the marketing chrome per the v1 layout
// convention; only /app bypasses it.
//
// Server-rendered: we hit the backend with the user's auth header
// directly (no /api proxy round-trip needed when we're already
// server-side) and render the result. No mock data.

import Link from "next/link";
import { ADVISOR_API_URL } from "@/lib/api";
import {
  BillingMeResponse,
  CaseListResponse,
  PurchaseHistoryResponse,
  Tier,
  TIER_DISPLAY,
  formatCurrency,
} from "@/lib/cases";
import { buildAdvisorAuthHeaders } from "@/lib/advisor-auth";
import { Btn } from "@/components/btn";
import { Mono } from "@/components/mono";

export const dynamic = "force-dynamic";

const TIER_ORDER: Tier[] = ["quick", "standard", "complex"];


async function fetchAuthed<T>(path: string): Promise<T | { _unauthorized: true } | null> {
  const headers = await buildAdvisorAuthHeaders();
  if (headers === null) return { _unauthorized: true };
  try {
    const r = await fetch(`${ADVISOR_API_URL}${path}`, {
      cache: "no-store",
      headers: { Accept: "application/json", ...headers },
    });
    if (r.status === 401) return { _unauthorized: true };
    if (!r.ok) return null;
    return (await r.json()) as T;
  } catch {
    return null;
  }
}


export default async function BillingPage() {
  const [me, purchases, cases] = await Promise.all([
    fetchAuthed<BillingMeResponse>("/v1/billing/me"),
    fetchAuthed<PurchaseHistoryResponse>("/v1/billing/purchases"),
    fetchAuthed<CaseListResponse>("/v1/cases"),
  ]);

  const unauthorized =
    (me && "_unauthorized" in me) ||
    (purchases && "_unauthorized" in purchases) ||
    (cases && "_unauthorized" in cases);

  return (
    <div
      className="px-5 sm:px-8 py-10 sm:py-12 lg:py-14 mx-auto max-w-[1100px]"
      style={{ minHeight: "calc(100vh - 280px)" }}
    >
      <header className="flex flex-col gap-3 sm:gap-3.5 pb-6 sm:pb-7 mb-7 sm:mb-9 border-b border-hair">
        <Mono muted size={11}>
          ACCOUNT · CASE CREDITS
        </Mono>
        <h1
          className="font-sans font-extrabold m-0 text-[28px] sm:text-[36px] lg:text-[42px] leading-[1]"
          style={{ letterSpacing: "-0.04em" }}
        >
          Billing
        </h1>
      </header>

      {unauthorized ? (
        <UnauthorizedCard />
      ) : (
        <div className="flex flex-col gap-9 sm:gap-12">
          <BalanceCard me={me as BillingMeResponse | null} />
          <PurchasesCard
            purchases={(purchases as PurchaseHistoryResponse | null)?.purchases ?? []}
          />
          <CasesCard
            cases={(cases as CaseListResponse | null)?.cases ?? []}
          />
        </div>
      )}
    </div>
  );
}


function UnauthorizedCard() {
  return (
    <div className="bg-surface-alt border border-hair p-8">
      <div className="font-semibold mb-2">Sign in to view your billing</div>
      <div className="text-text-muted text-[13.5px] mb-4">
        Your case-credit balance and purchase history live behind your
        account.
      </div>
      <Link href="/login?next=/billing">
        <Btn variant="primary" size="sm">
          Sign in
        </Btn>
      </Link>
    </div>
  );
}


function BalanceCard({ me }: { me: BillingMeResponse | null }) {
  const balances = new Map(
    (me?.tier_balances ?? []).map((b) => [b.tier, b]),
  );
  const total = me?.total_available_credits ?? 0;
  const enabled = me?.enabled ?? false;
  return (
    <section>
      <div className="flex items-baseline justify-between mb-3 sm:mb-4">
        <h2
          className="font-sans font-extrabold m-0 text-[20px] sm:text-[24px]"
          style={{ letterSpacing: "-0.03em" }}
        >
          Credit balance
        </h2>
        <Link href="/pricing" className="text-[12.5px] underline text-text-muted">
          Buy more
        </Link>
      </div>

      {!enabled && (
        <div className="bg-surface-alt border border-hair p-4 mb-3 text-[13px] text-text-muted">
          Billing is dormant on this deployment. Purchases are not yet
          available; admin can grant credits manually for beta access.
        </div>
      )}

      <div className="grid grid-cols-1 sm:grid-cols-3 gap-3 sm:gap-3.5">
        {TIER_ORDER.map((tier) => {
          const b = balances.get(tier);
          return (
            <div
              key={tier}
              className="bg-surface border border-hair p-5 flex flex-col gap-2"
            >
              <Mono size={11} muted>
                {TIER_DISPLAY[tier].toUpperCase()}
              </Mono>
              <div
                className="text-[36px] font-extrabold leading-none"
                style={{ letterSpacing: "-0.04em" }}
              >
                {b?.available ?? 0}
              </div>
              <div className="text-[12px] text-text-muted">
                available · {b?.reserved ?? 0} in flight ·{" "}
                {b?.consumed ?? 0} consumed
              </div>
            </div>
          );
        })}
      </div>

      <div className="text-[12.5px] text-text-muted mt-3">
        Total available across tiers: <strong>{total}</strong> credits.
      </div>
    </section>
  );
}


function PurchasesCard({
  purchases,
}: {
  purchases: PurchaseHistoryResponse["purchases"];
}) {
  return (
    <section>
      <h2
        className="font-sans font-extrabold m-0 mb-3 sm:mb-4 text-[20px] sm:text-[24px]"
        style={{ letterSpacing: "-0.03em" }}
      >
        Purchase history
      </h2>
      {purchases.length === 0 ? (
        <div className="bg-surface-alt border border-hair p-5 text-[13px] text-text-muted">
          No purchases yet. Visit{" "}
          <Link href="/pricing" className="underline">
            /pricing
          </Link>{" "}
          to buy your first pack.
        </div>
      ) : (
        <div className="border border-hair">
          <table className="w-full text-[13px] border-collapse">
            <thead>
              <tr className="bg-surface-alt text-left">
                <th className="px-4 py-2.5 font-mono text-[11px] uppercase text-text-muted">Date</th>
                <th className="px-4 py-2.5 font-mono text-[11px] uppercase text-text-muted">Pack</th>
                <th className="px-4 py-2.5 font-mono text-[11px] uppercase text-text-muted">Tier</th>
                <th className="px-4 py-2.5 font-mono text-[11px] uppercase text-text-muted text-right">Qty</th>
                <th className="px-4 py-2.5 font-mono text-[11px] uppercase text-text-muted text-right">Amount</th>
              </tr>
            </thead>
            <tbody>
              {purchases.map((p) => (
                <tr key={p.id} className="border-t border-hair">
                  <td className="px-4 py-2.5">
                    {new Date(p.created_at).toLocaleDateString("en-CA")}
                  </td>
                  <td className="px-4 py-2.5 capitalize">{p.pack_sku}</td>
                  <td className="px-4 py-2.5 capitalize">
                    {TIER_DISPLAY[p.tier]}
                  </td>
                  <td className="px-4 py-2.5 text-right">{p.quantity}</td>
                  <td className="px-4 py-2.5 text-right font-mono">
                    {formatCurrency(p.amount_paid_cents, p.currency)}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </section>
  );
}


function CasesCard({
  cases,
}: {
  cases: CaseListResponse["cases"];
}) {
  const recent = cases.slice(0, 8);
  return (
    <section>
      <div className="flex items-baseline justify-between mb-3 sm:mb-4">
        <h2
          className="font-sans font-extrabold m-0 text-[20px] sm:text-[24px]"
          style={{ letterSpacing: "-0.03em" }}
        >
          Recent cases
        </h2>
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
        <div className="border border-hair">
          <table className="w-full text-[13px] border-collapse">
            <thead>
              <tr className="bg-surface-alt text-left">
                <th className="px-4 py-2.5 font-mono text-[11px] uppercase text-text-muted">Anchor</th>
                <th className="px-4 py-2.5 font-mono text-[11px] uppercase text-text-muted">Tier</th>
                <th className="px-4 py-2.5 font-mono text-[11px] uppercase text-text-muted">Status</th>
                <th className="px-4 py-2.5 font-mono text-[11px] uppercase text-text-muted text-right">Last activity</th>
              </tr>
            </thead>
            <tbody>
              {recent.map((c) => (
                <tr key={c.id} className="border-t border-hair">
                  <td className="px-4 py-2.5 truncate max-w-[260px]">
                    {c.anchor_label}
                  </td>
                  <td className="px-4 py-2.5 capitalize">
                    {c.current_tier
                      ? TIER_DISPLAY[c.current_tier]
                      : "—"}
                  </td>
                  <td className="px-4 py-2.5 capitalize">{c.status}</td>
                  <td className="px-4 py-2.5 text-right text-text-muted">
                    {new Date(c.last_activity_at).toLocaleDateString("en-CA")}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </section>
  );
}
