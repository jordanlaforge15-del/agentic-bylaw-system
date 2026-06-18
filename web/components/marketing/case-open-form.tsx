// Case-open client form, migrated onto the priced-question catalog
// (ABS-320). The product no longer sells quick/standard/complex tiers —
// it sells answers to specific questions. The flow is now:
//
//   1. Anchor (address) input + in-window match lookup (unchanged — a
//      case is a free container, so finding an existing case lets the
//      user continue it without buying anything).
//   2. Pick a question from the live catalog (GET /api/billing/questions).
//   3. Catalog question → consultant-style intake gathers any required
//      inputs (POST /api/billing/questions/intake), then checkout
//      (POST /api/billing/checkout/question) → hosted-checkout redirect.
//
// ABS-325: the off-menu "Other" free-form path (type a question, get an
// LLM-quoted price, buy an answer) is disabled at launch — too open-ended
// to expose (unbounded scope / quoting / liability). The entry point is
// removed here; the backend quote/checkout-other endpoints are gated
// behind ADVISOR_OTHER_QUESTION_ENABLED (default false), so re-enabling is
// a flag flip plus restoring this UI, not a rebuild. Only the fixed
// catalog questions are sold.
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
  BillingMeResponse,
  CaseRow,
  FreeStartResponse,
  IntakeResponse,
  MatchResponse,
  QuestionCheckoutResponse,
  QuestionMenuItem,
  QuestionMenuResponse,
  TIER_DISPLAY,
  formatCurrency,
} from "@/lib/cases";
import { Btn } from "@/components/btn";
import { Mono } from "@/components/mono";

// Only "address" is backed by a live data source in beta (see ABS-200).
const ANCHOR_KIND_OPTIONS: AnchorKind[] = ["address"];

type Working =
  | "idle"
  | "matching"
  | "loadingMenu"
  | "intake"
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

  // Selection: a catalog slug, or null.
  const [selectedSlug, setSelectedSlug] = useState<string | null>(null);

  // Consultant-style intake state for the selected catalog question.
  const [conversation, setConversation] = useState(initialMessage);
  const [collectedInputs, setCollectedInputs] = useState<
    Record<string, string>
  >({});
  const [intake, setIntake] = useState<IntakeResponse | null>(null);

  const [working, setWorking] = useState<Working>("idle");
  const [error, setError] = useState<string | null>(null);

  // Free-question entitlement counter (ABS-314). null = not yet loaded
  // or not authenticated; 0 = exhausted; >0 = credits available.
  const [freeQuestionsRemaining, setFreeQuestionsRemaining] = useState<
    number | null
  >(null);

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

  // Load the user's free-question counter. Auth-required; 401 just means
  // the user is not signed in — silently keep null (no free-trial button).
  useEffect(() => {
    let cancelled = false;
    fetch("/api/billing/me")
      .then((r) => (r.ok ? r.json() : Promise.reject(r.status)))
      .then((data: BillingMeResponse) => {
        if (!cancelled)
          setFreeQuestionsRemaining(data.free_questions_remaining);
      })
      .catch(() => {
        /* unauthenticated or unavailable — leave null */
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

  // Payments-off path (ABS-320 / ABS-322): consume one free-question
  // entitlement and open the case, then route to the in-app answer view.
  async function startFreeAnswer(
    questionSlug: string,
    inputs: Record<string, string>,
  ) {
    if (!anchorLabel.trim()) {
      setError("Add a property anchor before continuing.");
      return;
    }
    setWorking("checkout");
    setError(null);
    try {
      let r: Response;
      try {
        r = await fetch("/api/billing/questions/free-start", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            question_slug: questionSlug,
            inputs,
            anchor_label: anchorLabel.trim(),
            anchor_kind: anchorKind,
          }),
        });
      } catch (e) {
        setError(
          `Network error: ${(e as Error).message}. Check your connection and try again.`,
        );
        return;
      }
      if (r.status === 401) return unauthorized();
      if (r.status === 402) {
        // Race: another tab consumed the last credit between the render
        // and this click. Update local counter so the UI flips to exhausted.
        setFreeQuestionsRemaining(0);
        return;
      }
      if (!r.ok) {
        const detail = await r.json().catch(() => null);
        setError(
          (detail?.detail as { message?: string } | undefined)?.message ??
            `Couldn't start free answer (${r.status}).`,
        );
        return;
      }
      const data = (await r.json()) as FreeStartResponse;
      setFreeQuestionsRemaining(data.free_questions_remaining);
      router.push(`/app?case_id=${data.case_id}`);
    } finally {
      setWorking((w) => (w === "checkout" ? "idle" : w));
    }
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
        // Payments-off: use free-question entitlement instead of Stripe.
        if (!menu?.enabled) {
          await startFreeAnswer(selectedQuestion.slug, data.inputs);
        } else {
          await checkoutQuestion(selectedQuestion.slug, data.inputs);
        }
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
          ) : freeQuestionsRemaining !== null && freeQuestionsRemaining > 0 ? (
            <div className="flex gap-2 mt-2">
              <Btn
                variant="accent"
                size="md"
                onClick={runIntake}
                disabled={busy}
                data-testid="free-trial-btn"
              >
                {working === "intake"
                  ? "Checking…"
                  : working === "checkout"
                    ? "Starting…"
                    : "Get answer (free trial)"}
              </Btn>
            </div>
          ) : freeQuestionsRemaining === 0 ? (
            <div
              className="mt-2 text-[12.5px] text-text-muted"
              data-testid="free-trial-exhausted"
            >
              Free trial used — paid answers coming soon.
            </div>
          ) : (
            <div className="mt-2 text-[12.5px] text-text-muted">
              Checkout isn&apos;t configured for this question yet.
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
