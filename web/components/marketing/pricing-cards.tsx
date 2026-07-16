// Client island for the "Pay by the turn" pricing page (ABS-387).
//
// Fetches the two data sources CLIENT-side through the Next proxy so the
// gate/posture subsets are stubbable in e2e and so the page can retry on a
// failed catalog load:
//   * GET /api/billing/topups   — the free-trial + paid top-up SKUs. Turn
//     counts and prices are backend-owned; we render them verbatim.
//   * GET /api/billing/questions — the per-report gate (ABS-384). Only the
//     enabled slugs come back, and each becomes a written-report SKU card.
//
// Payment posture is driven by topups.payments_enabled: when false (the beta
// posture) every top-up is "coming soon", checkout is impossible, and the
// private-beta banner is shown. When either fetch fails the page degrades to
// the designed PRICING UNAVAILABLE card with Retry — never a 500.

"use client";

import { useCallback, useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import Link from "next/link";
import { useAuth } from "@clerk/nextjs";
import { Btn } from "@/components/btn";
import { Mono } from "@/components/mono";
import {
  formatCurrency,
  type QuestionMenuResponse,
  type TopupCatalogResponse,
  type TopupOption,
} from "@/lib/cases";

// Real support address (not the mockup placeholder).
const SUPPORT_EMAIL = "info@agenticbylawsystems.com";

// Inlined at build time — mirror of the same guard in top-nav.tsx. When
// Clerk isn't really configured (fallback key), treat every visitor as
// signed-out so the CTAs route through /login rather than assuming a session.
const _PK = process.env.NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY ?? "";
const CLERK_ENABLED =
  /^pk_(test|live)_/.test(_PK) && _PK.length > 40 && !_PK.includes("replace");

type LoadState =
  | { status: "loading" }
  | { status: "error" }
  | {
      status: "ready";
      topups: TopupCatalogResponse;
      menu: QuestionMenuResponse;
    };

export function PricingCards() {
  const [state, setState] = useState<LoadState>({ status: "loading" });

  const load = useCallback(async () => {
    setState({ status: "loading" });
    try {
      const [topupsRes, menuRes] = await Promise.all([
        fetch("/api/billing/topups", { cache: "no-store" }),
        fetch("/api/billing/questions", { cache: "no-store" }),
      ]);
      if (!topupsRes.ok || !menuRes.ok) {
        setState({ status: "error" });
        return;
      }
      const topups = (await topupsRes.json()) as TopupCatalogResponse;
      const menu = (await menuRes.json()) as QuestionMenuResponse;
      setState({ status: "ready", topups, menu });
    } catch {
      setState({ status: "error" });
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  if (state.status === "loading") {
    return <LoadingGrid />;
  }
  if (state.status === "error") {
    return <PricingUnavailable onRetry={load} />;
  }

  const { topups, menu } = state;
  const reports = menu.questions; // gate returns enabled slugs only
  const hasReports = reports.length > 0;

  return (
    <>
      {!topups.payments_enabled && <BetaBanner />}

      {/* 4-up: inverted TrialCard + three TopUpCards. */}
      <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-3 sm:gap-3.5">
        <TrialCard />
        {topups.options.map((opt) => (
          <TopUpCard
            key={opt.sku}
            option={opt}
            currency={topups.currency}
            paymentsEnabled={topups.payments_enabled}
            bestValue={opt.sku === "medium"}
          />
        ))}
      </div>

      {hasReports && (
        <section
          className="mt-12 sm:mt-14 lg:mt-16"
          data-testid="reports-section"
        >
          <div className="flex flex-col gap-2 pb-5 mb-6 border-b border-hair">
            <Mono muted size={11}>
              {`WRITTEN REPORTS · ${reports.length} AVAILABLE`}
            </Mono>
            <p className="text-[13.5px] sm:text-[14.5px] text-text-muted leading-[1.5] m-0 max-w-[640px]">
              Prefer a fixed-price deliverable? Order a written report — a
              sourced answer to one question, grounded in the by-law and ready
              to attach to a permit or due-diligence file.
            </p>
          </div>
          <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-3 sm:gap-3.5">
            {reports.map((r) => (
              <ReportSku
                key={r.slug}
                slug={r.slug}
                name={r.display_name}
                priceCents={r.price_cents}
                currency={r.currency}
              />
            ))}
          </div>
        </section>
      )}

      <FaqGrid showJurisdiction={hasReports} />
    </>
  );
}


// ─── Trial (inverted, "ON SIGNUP") ──────────────────────────────────────────

const TRIAL_FEATURES = [
  "Included the moment you sign up",
  "Opening a case is always free",
  "Full citations, reasoning & sources",
  "No card required to start",
];

function TrialCard() {
  return (
    <div
      className="bg-surface-ink text-surface border-[1.5px] border-surface-ink p-5 flex flex-col gap-4 min-h-[300px]"
      data-testid="trial-card"
    >
      <div className="flex items-center justify-between">
        <span className="font-mono uppercase text-[10px] tracking-[0.14em] opacity-70">
          ON SIGNUP
        </span>
        <span className="font-mono uppercase text-[10px] tracking-[0.14em] opacity-70">
          ~10 turns
        </span>
      </div>
      <div>
        <div
          className="text-[40px] font-extrabold leading-none mb-1"
          style={{ letterSpacing: "-0.03em" }}
        >
          Free
        </div>
        <div className="text-[12px] opacity-70">Free trial turns</div>
      </div>
      <ul className="flex-1 flex flex-col gap-2 m-0 p-0 list-none">
        {TRIAL_FEATURES.map((f) => (
          <li key={f} className="flex items-start gap-2 text-[13px]">
            <span aria-hidden className="text-accent leading-[1.4]">
              ✓
            </span>
            <span className="leading-[1.4] opacity-90">{f}</span>
          </li>
        ))}
      </ul>
      <Link href="/signup" className="contents">
        <Btn variant="accent" size="sm" className="w-full">
          Request an invite →
        </Btn>
      </Link>
    </div>
  );
}


// ─── Top-up SKUs ────────────────────────────────────────────────────────────

function TopUpCard({
  option,
  currency,
  paymentsEnabled,
  bestValue,
}: {
  option: TopupOption;
  currency: string;
  paymentsEnabled: boolean;
  bestValue: boolean;
}) {
  const comingSoon = !paymentsEnabled || !option.available;
  return (
    <div
      className={`bg-surface p-5 flex flex-col gap-4 min-h-[300px] ${
        comingSoon
          ? "border-[1.5px] border-dashed border-hair"
          : "border-[1.5px] border-hair"
      }`}
      data-testid={`topup-card-${option.sku}`}
    >
      <div className="flex items-center justify-between gap-2">
        <Mono muted size={10}>
          TOP UP
        </Mono>
        <div className="flex items-center gap-2.5">
          {bestValue && (
            <span className="font-mono uppercase text-[10px] tracking-[0.14em] text-accent-ink">
              BEST VALUE
            </span>
          )}
          {comingSoon && (
            <span className="font-mono uppercase text-[10px] tracking-[0.14em] text-brick">
              COMING SOON
            </span>
          )}
        </div>
      </div>
      <div>
        <div
          className="text-[36px] font-extrabold leading-none mb-1"
          style={{ letterSpacing: "-0.03em" }}
        >
          ~{option.approx_turns} turns
        </div>
        <div className="text-[13px] text-text-muted">
          {formatCurrency(option.price_cents, currency)} · one-time
        </div>
      </div>
      <div className="flex-1 text-[12.5px] text-text-muted leading-[1.5]">
        Turn counts are approximate — a longer, more complex reply draws more
        from your balance than a short one.
      </div>
      <TopUpButton
        sku={option.sku}
        available={option.available && paymentsEnabled}
      />
    </div>
  );
}

function TopUpButton({
  sku,
  available,
}: {
  sku: string;
  available: boolean;
}) {
  const router = useRouter();
  const { isLoaded, isSignedIn } = useAuth();
  const signedIn = CLERK_ENABLED && isLoaded && isSignedIn;
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  if (!available) {
    return (
      <Btn variant="quiet" size="sm" className="w-full" disabled>
        Coming soon
      </Btn>
    );
  }

  async function onClick() {
    if (!signedIn) {
      router.push("/login?next=/pricing");
      return;
    }
    if (busy) return;
    setBusy(true);
    setError(null);
    try {
      const r = await fetch("/api/billing/checkout/topup", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ sku }),
      });
      if (r.status === 401) {
        router.push("/login?next=/pricing");
        return;
      }
      if (!r.ok) {
        setError(`Checkout failed (${r.status}).`);
        return;
      }
      const data = (await r.json()) as { url?: string };
      if (data.url) {
        window.location.href = data.url;
        return;
      }
      setError("Checkout returned no URL. Try again.");
    } catch (e) {
      setError((e as Error).message || "Network error.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="flex flex-col gap-1.5">
      <Btn
        variant="primary"
        size="sm"
        className="w-full"
        onClick={onClick}
        disabled={busy}
      >
        {busy
          ? "Opening checkout…"
          : signedIn
            ? "Top up →"
            : "Log in to top up →"}
      </Btn>
      {error && <span className="text-[11px] text-brick">{error}</span>}
    </div>
  );
}


// ─── Report SKUs ────────────────────────────────────────────────────────────

function ReportSku({
  slug,
  name,
  priceCents,
  currency,
}: {
  slug: string;
  name: string;
  priceCents: number;
  currency: string;
}) {
  return (
    <div
      className="bg-surface-alt border border-hair p-5 flex flex-col gap-3 min-h-[200px]"
      data-testid={`report-sku-${slug}`}
    >
      <Mono muted size={10}>
        FIXED-PRICE REPORT · ONE-TIME
      </Mono>
      <div className="flex-1">
        <div
          className="text-[28px] font-extrabold leading-none mb-1.5"
          style={{ letterSpacing: "-0.03em" }}
        >
          {formatCurrency(priceCents, currency)}
        </div>
        <div
          className="font-semibold text-[14px] sm:text-[15px]"
          style={{ letterSpacing: "-0.01em" }}
        >
          {name}
        </div>
      </div>
      <OrderReportButton slug={slug} />
    </div>
  );
}

function OrderReportButton({ slug }: { slug: string }) {
  const router = useRouter();
  const { isLoaded, isSignedIn } = useAuth();
  const signedIn = CLERK_ENABLED && isLoaded && isSignedIn;

  function onClick() {
    // No direct checkout from pricing — route into the case-open intake
    // (authed) or login first.
    const next = `/cases/new?report=${encodeURIComponent(slug)}`;
    router.push(signedIn ? next : `/login?next=${encodeURIComponent(next)}`);
  }

  return (
    <Btn variant="ghost" size="sm" className="w-full" onClick={onClick}>
      Order report →
    </Btn>
  );
}


// ─── FAQ + contact ──────────────────────────────────────────────────────────

const TURN_FAQS = [
  {
    q: "What's a turn?",
    a: "A turn is one reply from the advisor. Opening a case is free; each reply you get back draws tokens from your balance. Reading past replies and your open cases stays free.",
  },
  {
    q: "Why is my turn count approximate?",
    a: "Each reply uses a variable number of tokens depending on how much of the by-law it has to read and reason over. We show ~N turns as an estimate from a typical reply — a long, complex answer costs more than a short one.",
  },
  {
    q: "What happens when I run out?",
    a: "When your balance runs low you can top up any time. Your cases and past answers stay accessible; you only need a balance to get new replies.",
  },
];

const JURISDICTION_FAQ = {
  q: "What jurisdictions are supported?",
  a: "Halifax Regional Centre (the urban core of HRM, governed by the Regional Centre Land Use By-law) during private beta. More HRM bylaws are coming through 2026.",
};

function FaqGrid({ showJurisdiction }: { showJurisdiction: boolean }) {
  return (
    <div
      className="mt-9 sm:mt-12 lg:mt-14 grid grid-cols-1 sm:grid-cols-2 gap-3 sm:gap-4 lg:gap-[18px]"
      data-testid="pricing-faq"
    >
      {TURN_FAQS.map((f) => (
        <FaqCard key={f.q} q={f.q} a={f.a} />
      ))}
      {showJurisdiction ? (
        <FaqCard q={JURISDICTION_FAQ.q} a={JURISDICTION_FAQ.a} />
      ) : (
        <ReportContactCard />
      )}
    </div>
  );
}

function FaqCard({ q, a }: { q: string; a: string }) {
  return (
    <div className="bg-surface-alt border border-hair p-5 sm:p-[20px_22px]">
      <div
        className="text-[14px] sm:text-[15px] font-semibold mb-1.5"
        style={{ letterSpacing: "-0.01em" }}
      >
        {q}
      </div>
      <div className="text-[13px] sm:text-[13.5px] text-text-muted leading-[1.5]">
        {a}
      </div>
    </div>
  );
}

function ReportContactCard() {
  return (
    <div
      className="bg-surface-ink text-surface border-[1.5px] border-surface-ink p-5 sm:p-[20px_22px] flex flex-col gap-2"
      data-testid="report-contact-card"
    >
      <span className="font-mono uppercase text-[10px] tracking-[0.14em] opacity-70">
        NEED A WRITTEN REPORT?
      </span>
      <div className="text-[14px] sm:text-[15px] leading-[1.5] opacity-90">
        Written reports aren&apos;t open for self-serve ordering yet. Get in
        touch and we&apos;ll scope a fixed-price deliverable for your question.
      </div>
      <a
        className="text-[13px] font-semibold underline underline-offset-2 self-start"
        href={`mailto:${SUPPORT_EMAIL}`}
      >
        {SUPPORT_EMAIL} →
      </a>
    </div>
  );
}


// ─── Posture: beta banner, degraded card, loading ───────────────────────────

function BetaBanner() {
  return (
    <div
      className="mb-6 sm:mb-7 border-[1.5px] border-brick bg-surface-alt px-4 py-3 flex items-start gap-3"
      data-testid="beta-banner"
    >
      <span
        aria-hidden
        className="mt-[2px] inline-block w-2 h-2 shrink-0"
        style={{ background: "var(--brick)" }}
      />
      <p className="m-0 text-[13px] sm:text-[13.5px] leading-[1.5]">
        <span className="font-semibold" style={{ color: "var(--brick)" }}>
          You&apos;re in the private beta.
        </span>{" "}
        The conversation runs on free trial turns; paid top-ups open soon.
      </p>
    </div>
  );
}

function PricingUnavailable({ onRetry }: { onRetry: () => void }) {
  return (
    <div
      className="bg-surface-alt border border-hair p-8 text-center flex flex-col items-center gap-3"
      data-testid="pricing-unavailable"
    >
      <Mono muted size={11}>
        PRICING UNAVAILABLE
      </Mono>
      <div className="text-[14px] font-semibold">
        We couldn&apos;t load the pricing catalog.
      </div>
      <div className="text-text-muted text-[13.5px] max-w-[440px]">
        This is usually momentary. Retry, or reach us at{" "}
        <a className="underline" href={`mailto:${SUPPORT_EMAIL}`}>
          {SUPPORT_EMAIL}
        </a>{" "}
        for direct pricing.
      </div>
      <Btn
        variant="ghost"
        size="sm"
        onClick={onRetry}
        data-testid="pricing-retry"
      >
        Retry
      </Btn>
    </div>
  );
}

function LoadingGrid() {
  return (
    <div
      className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-3 sm:gap-3.5"
      data-testid="pricing-loading"
      aria-busy="true"
    >
      {[0, 1, 2, 3].map((i) => (
        <div
          key={i}
          className="bg-surface-alt border border-hair min-h-[300px] animate-pulse"
        />
      ))}
    </div>
  );
}
