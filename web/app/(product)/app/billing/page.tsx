// /app/billing — in-app account & billing view. Fetches the user's
// credit balance, purchase history, and cases from the backend, then
// renders them within the app shell (header + sidebar). Same content
// as /billing (the marketing site billing page) but keeps the user
// in the app chrome with immediate navigation back to chat.

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

export default async function AppBillingPage() {
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
    <div className="flex flex-col h-dvh bg-surface text-text overflow-hidden">
      {/* App header */}
      <div className="border-b border-hair bg-surface flex items-center justify-between px-3 sm:px-5 py-2.5 sm:py-3 safe-pt safe-px gap-2">
        <div className="flex items-center gap-2.5 sm:gap-3.5 min-w-0">
          <Link href="/app" className="flex items-center gap-2 text-text-muted hover:text-text transition-colors">
            <svg width="16" height="16" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="2">
              <path d="M13 8H3m0 0l6-6m-6 6l6 6" />
            </svg>
            <span className="text-sm">Back to chat</span>
          </Link>
        </div>
        <div className="flex items-center gap-2 sm:gap-2.5 flex-shrink-0">
          <Mono muted className="hidden lg:inline">
            VERIFIED 2026·05·06
          </Mono>
        </div>
      </div>

      {/* Main content */}
      <div className="flex-1 overflow-y-auto">
        <div
          className="px-5 sm:px-8 py-10 sm:py-12 lg:py-14 mx-auto max-w-[1100px]"
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
      </div>
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
      <Link href="/sign-in">
        <Btn variant="primary" size="sm">
          Sign in
        </Btn>
      </Link>
    </div>
  );
}

function BalanceCard({ me }: { me: BillingMeResponse | null }) {
  if (!me) {
    return (
      <div className="bg-surface-alt border border-hair p-8">
        <div className="font-semibold mb-2">Couldn&apos;t load balance</div>
        <div className="text-text-muted text-[13.5px]">
          Please try refreshing the page.
        </div>
      </div>
    );
  }

  const balances = new Map(me.tier_balances.map((b) => [b.tier, b]));

  return (
    <div className="bg-surface-alt border border-hair p-8 rounded">
      {!me.enabled && (
        <div className="bg-surface border border-hair p-4 mb-6 text-[13px] text-text-muted">
          Billing is dormant on this deployment. Purchases are not yet
          available; admin can grant credits manually for beta access.
        </div>
      )}

      <div className="mb-6">
        <Mono muted size={10} className="mb-2">
          AVAILABLE CREDITS
        </Mono>
        <div className="flex items-baseline gap-2">
          <span
            className="font-semibold text-[48px] leading-[1]"
            style={{ fontVariantNumeric: "tabular-nums" }}
          >
            {me.total_available_credits}
          </span>
          <span className="text-text-muted text-[14px]">credits</span>
        </div>
        <div className="text-text-muted text-[13.5px] mt-2">
          Available across all tiers
        </div>
      </div>

      <div className="grid gap-4 grid-cols-1 sm:grid-cols-3 my-6">
        {TIER_ORDER.map((tier) => {
          const b = balances.get(tier);
          return (
            <div key={tier} className="bg-surface border border-hair p-4 rounded">
              <Mono muted size={9} className="mb-2 block">
                {TIER_DISPLAY[tier].toUpperCase()}
              </Mono>
              <div className="text-[28px] font-semibold leading-[1] mb-1">
                {b?.available ?? 0}
              </div>
              <div className="text-[11.5px] text-text-muted">
                available · {b?.reserved ?? 0} in flight · {b?.consumed ?? 0} consumed
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
}

function PurchasesCard({
  purchases,
}: {
  purchases: PurchaseHistoryResponse["purchases"];
}) {
  return (
    <div className="bg-surface-alt border border-hair rounded overflow-hidden">
      <div className="p-6 border-b border-hair">
        <Mono muted size={10} className="mb-3">
          PURCHASE HISTORY
        </Mono>
        <h2 className="text-[20px] font-semibold m-0">Recent transactions</h2>
      </div>
      {purchases.length === 0 ? (
        <div className="p-6 text-text-muted text-[13.5px]">
          No purchases yet.
        </div>
      ) : (
        <div className="divide-y divide-hair">
          {purchases.map((p) => (
            <div key={p.id} className="p-6 flex justify-between items-start">
              <div>
                <div className="font-semibold mb-1 capitalize">
                  {TIER_DISPLAY[p.tier]} · {p.pack_sku} ({p.quantity}×)
                </div>
                <div className="text-[12.5px] text-text-muted">
                  {new Date(p.created_at).toLocaleDateString("en-CA", {
                    year: "numeric",
                    month: "long",
                    day: "numeric",
                  })}
                </div>
              </div>
              <div className="text-right">
                <div className="font-semibold">
                  {formatCurrency(p.amount_paid_cents, p.currency)}
                </div>
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

function CasesCard({
  cases,
}: {
  cases: CaseListResponse["cases"];
}) {
  const recent = cases.slice(0, 8);
  return (
    <div className="bg-surface-alt border border-hair rounded overflow-hidden">
      <div className="p-6 border-b border-hair">
        <Mono muted size={10} className="mb-3">
          ACTIVE CASES
        </Mono>
        <h2 className="text-[20px] font-semibold m-0">Recent research</h2>
      </div>
      {recent.length === 0 ? (
        <div className="p-6 text-text-muted text-[13.5px]">
          No cases yet.{" "}
          <Link href="/cases/new" className="underline text-text">
            Start a new case
          </Link>
          .
        </div>
      ) : (
        <div className="divide-y divide-hair">
          {recent.map((c) => (
            <Link
              key={c.id}
              href={`/app?case_id=${c.id}`}
              className="p-6 flex justify-between items-start hover:bg-surface transition-colors cursor-pointer"
            >
              <div className="flex-1">
                <div className="font-semibold mb-1 truncate max-w-[320px]">
                  {c.anchor_label || `Case #${c.user_case_number}`}
                </div>
                <div className="text-[12.5px] text-text-muted">
                  {new Date(c.last_activity_at).toLocaleDateString("en-CA", {
                    year: "numeric",
                    month: "long",
                    day: "numeric",
                  })}
                </div>
              </div>
              <div className="text-right text-text-muted">
                <div className="text-[12.5px] font-mono capitalize">
                  {c.current_tier ? TIER_DISPLAY[c.current_tier] : "—"}
                </div>
                <div className="text-[11px] capitalize">{c.status}</div>
              </div>
            </Link>
          ))}
        </div>
      )}
    </div>
  );
}
