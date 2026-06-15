// Case-open client form, migrated onto the priced-question catalog
// (ABS-320). The product no longer sells quick/standard/complex tiers —
// it sells answers to specific questions. The flow is now:
//
//   1. Anchor (address) input + in-window match lookup (unchanged — a
//      case is a free container, so finding an existing case lets the
//      user continue it without buying anything).
//   2. Pick a question from the live catalog (GET /api/billing/questions),
//      or the "Other" free-form path.
//   3. Catalog question → consultant-style intake gathers any required
//      inputs (POST /api/billing/questions/intake), then checkout
//      (POST /api/billing/checkout/question) → hosted-checkout redirect.
//      "Other" → free quote (POST /api/billing/questions/quote) → buy
//      (POST /api/billing/checkout/other) → hosted-checkout redirect.
//
// Stays in /components/marketing/ alongside the rest of the account
// chrome. When billing is dormant (no payment processor configured) the
// catalog marks every question `available: false`; the form still renders
// the menu and disables purchase, mirroring buy-question-button.tsx so the
// page is usable before checkout is wired.

"use client";

import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import {
  ANCHOR_KIND_DISPLAY,
  AnchorKind,
  CaseRow,
  IntakeResponse,
  MatchResponse,
  OtherCheckoutResponse,
  QuestionCheckoutResponse,
  QuestionMenuItem,
  QuestionMenuResponse,
  QuoteResponse,
  TIER_DISPLAY,
  formatCurrency,
} from "@/lib/cases";
import { Btn } from "@/components/btn";
import { Mono } from "@/components/mono";

// Only "address" is backed by a live data source in beta (see ABS-200).
const ANCHOR_KIND_OPTIONS: AnchorKind[] = ["address"];

// Sentinel slug for the off-menu "Other" path. Not a catalog question —
// it routes to the free-quote → buy flow rather than intake → checkout.
const OTHER_SLUG = "__other__";

type Working =
  | "idle"
  | "matching"
  | "loadingMenu"
  | "intake"
  | "quoting"
  | "checkout";

export function CaseOpenForm({
  initialAnchorLabel = "",
  initialMessage = "",
}: {
  initialAnchorLabel?: string;
  initialMessage?: string;
}) {
  const router = useRouter();
  const [anchorLabel, setAnchorLabel] = useState(initialAnchorLabel);
  const [anchorKind, setAnchorKind] = useState<AnchorKind>("address");
  const [match, setMatch] = useState<CaseRow | null | undefined>(undefined);

  // Catalog menu, loaded once on mount.
  const [menu, setMenu] = useState<QuestionMenuResponse | null>(null);
  const [menuError, setMenuError] = useState(false);

  // Selection: a catalog slug, OTHER_SLUG, or null.
  const [selectedSlug, setSelectedSlug] = useState<string | null>(null);

  // Consultant-style intake state for the selected catalog question.
  const [conversation, setConversation] = useState(initialMessage);
  const [collectedInputs, setCollectedInputs] = useState<
    Record<string, string>
  >({});
  const [intake, setIntake] = useState<IntakeResponse | null>(null);

  // "Other" free-form path state.
  const [otherQuestion, setOtherQuestion] = useState(initialMessage);
  const [quote, setQuote] = useState<QuoteResponse | null>(null);

  const [working, setWorking] = useState<Working>("idle");
  const [error, setError] = useState<string | null>(null);

  // Load the live question catalog. Mirrors the pricing page: dormant
  // billing renders the menu with every question `available: false`.
  useEffect(() => {
    let cancelled = false;
    setWorking("loadingMenu");
    fetch("/api/billing/questions")
      .then((r) => (r.ok ? r.json() : Promise.reject(r.status)))
      .then((data: QuestionMenuResponse) => {
        if (!cancelled) setMenu(data);
      })
      .catch(() => {
        if (!cancelled) setMenuError(true);
      })
      .finally(() => {
        if (!cancelled) setWorking("idle");
      });
    return () => {
      cancelled = true;
    };
  }, []);

  const selectedQuestion: QuestionMenuItem | undefined =
    menu?.questions.find((q) => q.slug === selectedSlug);

  function resetSelectionState() {
    setIntake(null);
    setCollectedInputs({});
    setQuote(null);
    setError(null);
  }

  async function lookupMatch() {
    if (!anchorLabel.trim()) return;
    setWorking("matching");
    setMatch(undefined);
    try {
      const r = await fetch(
        `/api/cases/match?anchor_label=${encodeURIComponent(anchorLabel)}&anchor_kind=${encodeURIComponent(anchorKind)}`,
      );
      if (!r.ok) {
        setMatch(null);
        return;
      }
      const data = (await r.json()) as MatchResponse;
      setMatch(data.case);
    } finally {
      setWorking("idle");
    }
  }

  function unauthorized() {
    router.push("/login?next=/cases/new");
  }

  // Redirect the browser to a hosted-checkout URL.
  function goToCheckout(url: string | undefined) {
    if (url) {
      window.location.href = url;
      return true;
    }
    setError("Checkout returned no URL. Try again.");
    return false;
  }

  // Catalog question: run one intake pass. The anchor address is seeded as
  // a collected input so an address-bearing question never re-asks for it.
  async function runIntake() {
    if (!selectedQuestion) return;
    setWorking("intake");
    setError(null);
    const seeded: Record<string, string> = { ...collectedInputs };
    if (anchorKind === "address" && anchorLabel.trim() && !seeded.address) {
      seeded.address = anchorLabel.trim();
    }
    try {
      let r: Response;
      try {
        r = await fetch("/api/billing/questions/intake", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            question_slug: selectedQuestion.slug,
            conversation,
            inputs: seeded,
          }),
        });
      } catch (e) {
        setError(
          `Network error: ${(e as Error).message}. Check your connection and try again.`,
        );
        return;
      }
      if (r.status === 401) return unauthorized();
      if (!r.ok) {
        const detail = await r.json().catch(() => null);
        setError(
          (detail?.detail as { message?: string } | undefined)?.message ??
            `Couldn't process intake (${r.status}).`,
        );
        return;
      }
      const data = (await r.json()) as IntakeResponse;
      setIntake(data);
      setCollectedInputs(data.inputs);
      // Reflect any newly-extracted address back onto the anchor field so
      // the two stay consistent.
      if (data.inputs.address && anchorKind === "address" && !anchorLabel) {
        setAnchorLabel(data.inputs.address);
      }
      if (data.complete) {
        await checkoutQuestion(selectedQuestion.slug, data.inputs);
      }
    } finally {
      setWorking((w) => (w === "intake" ? "idle" : w));
    }
  }

  // Catalog question checkout: hand the collected inputs to the backend,
  // which authorizes the card and returns a hosted-checkout URL.
  async function checkoutQuestion(
    slug: string,
    inputs: Record<string, string>,
  ) {
    setWorking("checkout");
    setError(null);
    try {
      let r: Response;
      try {
        r = await fetch("/api/billing/checkout/question", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ question_slug: slug, inputs }),
        });
      } catch (e) {
        setError(
          `Network error opening checkout: ${(e as Error).message}. Check your connection and try again.`,
        );
        return;
      }
      if (r.status === 401) return unauthorized();
      if (!r.ok) {
        const detail = await r.json().catch(() => null);
        setError(
          (detail?.detail as { message?: string } | undefined)?.message ??
            `Checkout failed (${r.status}).`,
        );
        return;
      }
      const data = (await r.json()) as QuestionCheckoutResponse & {
        url?: string;
      };
      goToCheckout(data.url);
    } finally {
      setWorking((w) => (w === "checkout" ? "idle" : w));
    }
  }

  // "Other": free quote first (no charge), then the user confirms to buy.
  async function getQuote() {
    if (!otherQuestion.trim()) {
      setError("Describe your question to get a quote.");
      return;
    }
    setWorking("quoting");
    setError(null);
    try {
      let r: Response;
      try {
        r = await fetch("/api/billing/questions/quote", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ question: otherQuestion.trim() }),
        });
      } catch (e) {
        setError(
          `Network error: ${(e as Error).message}. Check your connection and try again.`,
        );
        return;
      }
      if (r.status === 401) return unauthorized();
      if (!r.ok) {
        const detail = await r.json().catch(() => null);
        setError(
          (detail?.detail as { message?: string } | undefined)?.message ??
            `Couldn't quote that question (${r.status}).`,
        );
        return;
      }
      setQuote((await r.json()) as QuoteResponse);
    } finally {
      setWorking((w) => (w === "quoting" ? "idle" : w));
    }
  }

  // "Other" checkout: the backend re-quotes server-side and authorizes.
  async function buyOther() {
    if (!otherQuestion.trim()) return;
    setWorking("checkout");
    setError(null);
    try {
      let r: Response;
      try {
        r = await fetch("/api/billing/checkout/other", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ question: otherQuestion.trim() }),
        });
      } catch (e) {
        setError(
          `Network error opening checkout: ${(e as Error).message}. Check your connection and try again.`,
        );
        return;
      }
      if (r.status === 401) return unauthorized();
      if (!r.ok) {
        const detail = await r.json().catch(() => null);
        setError(
          (detail?.detail as { message?: string } | undefined)?.message ??
            `Checkout failed (${r.status}).`,
        );
        return;
      }
      const data = (await r.json()) as OtherCheckoutResponse & {
        url?: string;
      };
      goToCheckout(data.url);
    } finally {
      setWorking((w) => (w === "checkout" ? "idle" : w));
    }
  }

  const busy = working !== "idle";

  return (
    <div className="flex flex-col gap-6">
      <Field label="Anchor">
        <div className="flex gap-2">
          <select
            aria-label="Anchor kind"
            value={anchorKind}
            onChange={(e) => {
              setAnchorKind(e.target.value as AnchorKind);
              setMatch(undefined);
            }}
            className="bg-surface border border-hair px-3 py-2 text-[13.5px]"
          >
            {ANCHOR_KIND_OPTIONS.map((k) => (
              <option key={k} value={k}>
                {ANCHOR_KIND_DISPLAY[k]}
              </option>
            ))}
          </select>
          <input
            type="text"
            value={anchorLabel}
            onChange={(e) => {
              setAnchorLabel(e.target.value);
              setMatch(undefined);
              setError(null);
            }}
            onBlur={lookupMatch}
            placeholder="e.g. 1234 Main St, Halifax"
            className="flex-1 bg-surface border border-hair px-3 py-2 text-[13.5px]"
          />
        </div>
        <div className="text-[12px] text-text-muted mt-1.5">
          Anchor your inquiry to a property. Opening or continuing a case is
          free — you only pay when you buy an answer below.
        </div>
      </Field>

      {match !== undefined && match !== null && (
        <div className="bg-surface-alt border border-hair p-4">
          <Mono size={11} muted>
            EXISTING CASE FOUND
          </Mono>
          <div className="mt-1 text-[13.5px]">
            You opened a case for this anchor on{" "}
            {new Date(match.last_activity_at).toLocaleDateString("en-CA")}{" "}
            ({match.current_tier ? TIER_DISPLAY[match.current_tier] : "—"}).
            Continuing it is free.
          </div>
          <div className="mt-3 flex gap-2">
            <Btn
              variant="primary"
              size="sm"
              onClick={() => router.push(`/app?case_id=${match.id}`)}
            >
              Continue case
            </Btn>
            <Btn variant="quiet" size="sm" onClick={() => setMatch(null)}>
              Start new case anyway
            </Btn>
          </div>
        </div>
      )}

      <Field label="Choose a question">
        {menuError ? (
          <div className="bg-surface-alt border border-hair p-4 text-[13px] text-text-muted">
            We couldn&apos;t load the question menu. Refresh in a moment, or
            see{" "}
            <a className="underline" href="/pricing">
              pricing
            </a>
            .
          </div>
        ) : menu === null ? (
          <div className="text-[13px] text-text-muted">Loading questions…</div>
        ) : (
          <div
            className="grid grid-cols-1 sm:grid-cols-2 gap-3"
            data-testid="question-menu"
          >
            {menu.questions.map((q) => (
              <button
                key={q.slug}
                type="button"
                data-testid={`question-option-${q.slug}`}
                onClick={() => {
                  setSelectedSlug(q.slug);
                  resetSelectionState();
                }}
                className={`text-left border p-3 flex flex-col gap-1 transition-colors ${
                  selectedSlug === q.slug
                    ? "border-text"
                    : "border-hair hover:bg-surface-alt"
                }`}
              >
                <div className="flex items-baseline justify-between gap-2">
                  <span className="font-semibold text-[13.5px]">
                    {q.display_name}
                  </span>
                  <span className="font-mono text-[12px] whitespace-nowrap">
                    {formatCurrency(q.price_cents, q.currency)}
                  </span>
                </div>
                <span className="text-[12px] text-text-muted leading-[1.4]">
                  {q.summary}
                </span>
              </button>
            ))}
            <button
              type="button"
              data-testid="question-option-other"
              onClick={() => {
                setSelectedSlug(OTHER_SLUG);
                resetSelectionState();
              }}
              className={`text-left border p-3 flex flex-col gap-1 transition-colors ${
                selectedSlug === OTHER_SLUG
                  ? "border-text"
                  : "border-hair hover:bg-surface-alt"
              }`}
            >
              <span className="font-semibold text-[13.5px]">
                Other — my question isn&apos;t listed
              </span>
              <span className="text-[12px] text-text-muted leading-[1.4]">
                Describe it and we&apos;ll quote a price before you buy.
              </span>
            </button>
          </div>
        )}
      </Field>

      {/* Catalog question: consultant-style intake. */}
      {selectedQuestion && (
        <Field label="Describe your situation">
          <textarea
            value={conversation}
            onChange={(e) => {
              setConversation(e.target.value);
              setError(null);
            }}
            rows={4}
            placeholder="Tell us what you want answered. We'll ask for anything else we need before you pay."
            className="bg-surface border border-hair px-3 py-2 text-[13.5px] font-sans resize-y w-full"
          />

          {intake && !intake.complete && intake.prompt && (
            <div
              className="bg-surface-alt border border-hair p-4 mt-2"
              data-testid="intake-prompt"
            >
              <Mono size={11} muted>
                ONE MORE THING
              </Mono>
              <div className="mt-1 text-[13.5px]">{intake.prompt}</div>
            </div>
          )}

          {selectedQuestion.available ? (
            <div className="flex gap-2 mt-2">
              <Btn variant="accent" size="md" onClick={runIntake} disabled={busy}>
                {working === "intake"
                  ? "Checking…"
                  : working === "checkout"
                    ? "Opening checkout…"
                    : `Continue · ${formatCurrency(
                        selectedQuestion.price_cents,
                        selectedQuestion.currency,
                      )}`}
              </Btn>
            </div>
          ) : (
            <div className="mt-2 text-[12.5px] text-text-muted">
              Checkout isn&apos;t configured for this question yet.
            </div>
          )}
        </Field>
      )}

      {/* "Other" free-form path: free quote, then buy. */}
      {selectedSlug === OTHER_SLUG && (
        <Field label="Describe your question">
          <textarea
            value={otherQuestion}
            onChange={(e) => {
              setOtherQuestion(e.target.value);
              setQuote(null);
              setError(null);
            }}
            rows={4}
            placeholder="Describe the off-menu question you want answered."
            className="bg-surface border border-hair px-3 py-2 text-[13.5px] font-sans resize-y w-full"
          />

          {quote && (
            <div
              className="bg-surface-alt border border-hair p-4 mt-2"
              data-testid="other-quote"
            >
              <Mono size={11} muted>
                QUOTE · {quote.difficulty_display_name.toUpperCase()}
              </Mono>
              <div className="mt-1 text-[20px] font-extrabold">
                {formatCurrency(quote.price_cents, quote.currency)}
              </div>
              <div className="mt-1 text-[12.5px] text-text-muted">
                {quote.rationale}
              </div>
            </div>
          )}

          {menu && !menu.enabled ? (
            <div className="mt-2 text-[12.5px] text-text-muted">
              Off-menu checkout isn&apos;t configured yet.
            </div>
          ) : !quote ? (
            <div className="flex gap-2 mt-2">
              <Btn
                variant="quiet"
                size="sm"
                onClick={getQuote}
                disabled={busy}
              >
                {working === "quoting" ? "Quoting…" : "Get a price (free)"}
              </Btn>
            </div>
          ) : (
            <div className="flex gap-2 mt-2">
              <Btn
                variant="accent"
                size="md"
                onClick={buyOther}
                disabled={busy}
              >
                {working === "checkout"
                  ? "Opening checkout…"
                  : `Buy answer · ${formatCurrency(
                      quote.price_cents,
                      quote.currency,
                    )}`}
              </Btn>
              <Btn variant="quiet" size="sm" onClick={getQuote} disabled={busy}>
                Re-quote
              </Btn>
            </div>
          )}
        </Field>
      )}

      {error && <div className="text-[13px] text-red-600">{error}</div>}
    </div>
  );
}

function Field({
  label,
  children,
}: {
  label: string;
  children: React.ReactNode;
}) {
  return (
    <div className="flex flex-col gap-1.5">
      <Mono size={11} muted>
        {label.toUpperCase()}
      </Mono>
      {children}
    </div>
  );
}
