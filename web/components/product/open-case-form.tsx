// Open a case — the per-answer entry flow, rebuilt on the ABS° blueprint
// design system (ABS-334). Opening a case is free; the user anchors to a
// property, picks one priced question from the live catalog, describes the
// situation, and buys (or free-trials) the answer.
//
// This is the redesign of the former marketing/case-open-form.tsx. The
// BACKEND WIRING IS UNCHANGED — same live catalog (GET /api/billing/
// questions), same in-window match lookup, same consultant intake
// (POST .../questions/intake), free-start (payments-off), and hosted
// checkout. Only the presentation moved onto the design system:
// two-column priced question cards with a radio + price block, the
// combined anchor input box, and a sticky checkout summary footer.
//
// Test contract preserved verbatim (smoke 03, abs320, case-existing-match,
// case-open-network-error): data-testids `question-menu`,
// `question-option-${slug}`, `free-trial-btn`, `free-trial-exhausted`,
// `intake-prompt`; the "EXISTING CASE FOUND" / "Continue case" block; the
// "e.g. 1234 Main St, Halifax" placeholder; and the dormant-billing
// fallback copy.

"use client";

import { useEffect, useMemo, useState } from "react";
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
import { cn } from "@/lib/cn";

// Only "address" is backed by a live data source in beta (see ABS-200).
const ANCHOR_KIND_OPTIONS: AnchorKind[] = ["address"];

// Default selection mirrors the design (the due-diligence summary), so the
// checkout footer reads as a complete priced choice on first paint. Falls
// back to the first catalog entry if the slug ever leaves the menu.
const DEFAULT_SLUG = "due_diligence";

type Working = "idle" | "matching" | "loadingMenu" | "intake" | "checkout";

export function OpenCaseForm({
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

  const [menu, setMenu] = useState<QuestionMenuResponse | null>(null);
  const [menuError, setMenuError] = useState(false);

  const [selectedSlug, setSelectedSlug] = useState<string | null>(null);

  const [conversation, setConversation] = useState(initialMessage);
  const [collectedInputs, setCollectedInputs] = useState<
    Record<string, string>
  >({});
  const [intake, setIntake] = useState<IntakeResponse | null>(null);

  const [working, setWorking] = useState<Working>("idle");
  const [error, setError] = useState<string | null>(null);

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
        if (cancelled) return;
        setMenu(data);
        // Pre-select the default (or first) question so the checkout
        // footer shows a concrete priced choice immediately.
        setSelectedSlug((prev) => {
          if (prev) return prev;
          const def = data.questions.find((q) => q.slug === DEFAULT_SLUG);
          return def?.slug ?? data.questions[0]?.slug ?? null;
        });
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

  // Load the user's free-question counter. 401 = not signed in; stay null.
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

  const selectedQuestion: QuestionMenuItem | undefined = useMemo(
    () => menu?.questions.find((q) => q.slug === selectedSlug),
    [menu, selectedSlug],
  );

  // ABS-324 launch posture: /cases/new is Answers-only. The Conversation
  // continue-case entry stays hidden until conversation_enabled flips.
  const conversationEnabled = menu?.conversation_enabled ?? false;

  // ABS-348: does unlocking an answer cost real money (Stripe) or a free
  // credit? Payments-off is the go-live posture — an `available` question is
  // unlocked by consuming a free-question credit, NOT a paid checkout. The
  // CTA framing and the completion routing both branch on this, so a
  // billing-on/payments-off menu (available:true) takes the free-credit path
  // instead of dead-ending on a Stripe checkout that returns no URL.
  const paymentsEnabled = menu?.payments_enabled ?? false;

  function resetSelectionState() {
    setIntake(null);
    setCollectedInputs({});
    setError(null);
  }

  async function lookupMatch() {
    if (!conversationEnabled) return;
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
    } catch {
      // A dropped connection (Safari "Load failed") must degrade to
      // "no match", not bubble as an unhandled rejection.
      setMatch(null);
    } finally {
      setWorking("idle");
    }
  }

  function unauthorized() {
    router.push("/login?next=/cases/new");
  }

  function goToCheckout(url: string | undefined) {
    if (url) {
      window.location.href = url;
      return true;
    }
    setError("Checkout returned no URL. Try again.");
    return false;
  }

  // Payments-off path (ABS-322/324): consume one free-question entitlement
  // to open an Answers purchase, then route to the answer/refine view.
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
      router.push(`/app/answers/${data.purchase_id}`);
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
      if (data.inputs.address && anchorKind === "address" && !anchorLabel) {
        setAnchorLabel(data.inputs.address);
      }
      if (data.complete) {
        // ABS-348: route on payments, not the billing master switch. Only a
        // payments-on, available question goes to Stripe checkout; everything
        // else (dormant billing, OR billing-on/payments-off go-live) unlocks
        // via a free credit and lands on the answer view. Keying this on
        // `menu.enabled` sent the go-live posture to a Stripe checkout that
        // returns no URL, dead-ending after the credit was already consumed.
        const paidCheckout =
          selectedQuestion.available === true && paymentsEnabled;
        if (paidCheckout) {
          await checkoutQuestion(selectedQuestion.slug, data.inputs);
        } else {
          await startFreeAnswer(selectedQuestion.slug, data.inputs);
        }
      }
    } finally {
      setWorking((w) => (w === "intake" ? "idle" : w));
    }
  }

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
      // Defensive (ABS-348): a payments-off response carries a purchase_id
      // and a null URL (free-credit unlock, no Stripe session). Route to the
      // answer view instead of erroring on the missing checkout URL. With the
      // payments-aware routing above this branch shouldn't be reached in
      // payments-off mode, but this keeps checkoutQuestion self-consistent.
      if (!data.url && data.purchase_id) {
        router.push(`/app/answers/${data.purchase_id}`);
        return;
      }
      goToCheckout(data.url);
    } finally {
      setWorking((w) => (w === "checkout" ? "idle" : w));
    }
  }

  const busy = working !== "idle";

  // Which checkout affordance applies to the selected question. ABS-348: a
  // *paid* purchase requires payments to be ON — in payments-off mode an
  // `available` question is a free-credit unlock, so it takes the free-trial
  // affordance (and, if the user's credits are spent, the exhausted notice),
  // not a "$79" checkout.
  const canPurchase = selectedQuestion?.available === true && paymentsEnabled;
  const canFreeTrial =
    !canPurchase &&
    freeQuestionsRemaining !== null &&
    freeQuestionsRemaining > 0;
  const trialExhausted =
    !canPurchase &&
    selectedQuestion !== undefined &&
    freeQuestionsRemaining === 0;

  // The checkout footer's CTA label tracks the working state + unlock mode.
  const ctaLabel =
    working === "intake"
      ? "Checking…"
      : working === "checkout"
        ? paymentsEnabled
          ? "Opening checkout…"
          : "Starting…"
        : selectedQuestion && canPurchase
          ? `Get answer · ${formatCurrency(
              selectedQuestion.price_cents,
              selectedQuestion.currency,
            )}`
          : "Get answer (free trial) →";

  const anchorSummary = (
    anchorLabel ? anchorLabel.toUpperCase() : "NO ADDRESS YET"
  )
    .concat(" · ")
    .concat(ANCHOR_KIND_DISPLAY[anchorKind].toUpperCase());

  return (
    <div className="flex flex-col">
      {/* Anchor */}
      <section className="mb-11">
        <Mono muted size={10} className="block mb-3.5">
          ANCHOR
        </Mono>
        <div className="flex border-[1.5px] border-text bg-surface">
          <select
            aria-label="Anchor kind"
            value={anchorKind}
            onChange={(e) => {
              setAnchorKind(e.target.value as AnchorKind);
              setMatch(undefined);
            }}
            className="border-none bg-surface-alt text-text font-sans text-[14px] outline-none cursor-pointer flex-shrink-0"
            style={{ borderRight: "1px solid var(--hair)", padding: "13px 14px" }}
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
            className="flex-1 min-w-0 border-none bg-transparent text-text font-sans text-[14px] outline-none"
            style={{ padding: "13px 16px" }}
          />
        </div>
        <div className="flex justify-between items-center gap-4 mt-2.5 flex-wrap">
          <span className="text-[13px] text-text-muted leading-[1.45]">
            Opening or continuing a case is free — you only pay when you buy an
            answer below.
          </span>
          <span
            className="inline-flex items-center gap-[7px] border border-hair flex-shrink-0"
            style={{ padding: "4px 9px" }}
          >
            <span
              aria-hidden
              className="bg-accent"
              style={{ width: 5, height: 5 }}
            />
            <Mono muted size={9}>
              REUSES AN OPEN CASE · 30 DAYS
            </Mono>
          </span>
        </div>
      </section>

      {/* In-window match (Conversation product; hidden at launch). */}
      {conversationEnabled && match !== undefined && match !== null && (
        <section className="mb-11">
          <div className="bg-surface-alt border border-hair p-4">
            <Mono size={11} muted>
              EXISTING CASE FOUND
            </Mono>
            <div className="mt-1 text-[13.5px]">
              You opened a case for this anchor on{" "}
              {new Date(match.last_activity_at).toLocaleDateString("en-CA")} (
              {match.current_tier ? TIER_DISPLAY[match.current_tier] : "—"}).
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
        </section>
      )}

      {/* Choose a question */}
      <section className="mb-11">
        <Mono muted size={10} className="block mb-3.5">
          CHOOSE A QUESTION{menu ? ` · ${menu.questions.length}` : ""}
        </Mono>
        {menuError ? (
          <div className="bg-surface-alt border border-hair p-4 text-[13px] text-text-muted">
            We couldn&apos;t load the question menu. Refresh in a moment, or see{" "}
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
              <QuestionCard
                key={q.slug}
                q={q}
                selected={selectedSlug === q.slug}
                onSelect={() => {
                  setSelectedSlug(q.slug);
                  resetSelectionState();
                }}
              />
            ))}
          </div>
        )}
      </section>

      {/* Describe your situation */}
      {selectedQuestion && (
        <section className="mb-10">
          <Mono muted size={10} className="block mb-3.5">
            DESCRIBE YOUR SITUATION
          </Mono>
          <textarea
            value={conversation}
            onChange={(e) => {
              setConversation(e.target.value);
              setError(null);
            }}
            placeholder="Tell us what you want answered. We'll ask for anything else we need before you pay."
            className="w-full box-border border-[1.5px] border-text bg-surface text-text font-sans text-[14px] outline-none resize-y leading-[1.55]"
            style={{ padding: "14px 16px", minHeight: 132 }}
          />

          {intake && !intake.complete && intake.prompt && (
            <div
              className="bg-surface-alt border border-hair p-4 mt-2.5"
              data-testid="intake-prompt"
            >
              <Mono size={11} muted>
                ONE MORE THING
              </Mono>
              <div className="mt-1 text-[13.5px]">{intake.prompt}</div>
            </div>
          )}
        </section>
      )}

      {/* Checkout footer */}
      {selectedQuestion && (
        <section
          data-testid="checkout-summary"
          className="flex justify-between items-end gap-7 flex-wrap"
          style={{ borderTop: "1px solid var(--rule)", paddingTop: 22 }}
        >
          <div className="flex flex-col gap-[5px] min-w-[240px]">
            <Mono muted size={9.5}>
              YOUR ANSWER
            </Mono>
            <div className="text-[18px] font-bold tracking-[-0.02em]">
              {selectedQuestion.display_name}
            </div>
            <Mono muted size={9.5}>
              {anchorSummary}
            </Mono>
          </div>

          <div className="flex items-center gap-[22px]">
            <div className="text-right">
              <div
                className="text-[28px] font-extrabold leading-none"
                style={{ letterSpacing: "-0.035em" }}
              >
                {formatCurrency(
                  selectedQuestion.price_cents,
                  selectedQuestion.currency,
                )}
              </div>
              <Mono accent size={9} className="block mt-1">
                {canPurchase ? "PER ANSWER" : "FIRST ANSWER FREE · BETA"}
              </Mono>
            </div>

            {canPurchase ? (
              <Btn variant="accent" size="lg" onClick={runIntake} disabled={busy}>
                {ctaLabel}
              </Btn>
            ) : canFreeTrial ? (
              <Btn
                variant="accent"
                size="lg"
                onClick={runIntake}
                disabled={busy}
                data-testid="free-trial-btn"
              >
                {ctaLabel}
              </Btn>
            ) : trialExhausted ? (
              <div
                className="text-[12.5px] text-text-muted max-w-[220px]"
                data-testid="free-trial-exhausted"
              >
                Free trial used — paid answers coming soon.
              </div>
            ) : (
              <div className="text-[12.5px] text-text-muted max-w-[220px]">
                Checkout isn&apos;t configured for this question yet.
              </div>
            )}
          </div>
        </section>
      )}

      {error && <div className="mt-4 text-[13px] text-red-600">{error}</div>}

      <Mono muted size={9} className="block mt-[22px]">
        NOT LEGAL ADVICE · RESEARCH ONLY · VERIFY WITH HRM PLANNING
      </Mono>
    </div>
  );
}

// A square 16px radio: outlined when unselected, accent-filled with an
// onAccent check when selected.
function CaseRadio({ selected }: { selected: boolean }) {
  return (
    <span
      aria-hidden
      className={cn(
        "inline-flex items-center justify-center flex-shrink-0 font-mono transition-all duration-100",
        selected ? "bg-accent text-on-accent" : "bg-transparent",
      )}
      style={{
        width: 16,
        height: 16,
        marginTop: 1,
        fontSize: 10,
        lineHeight: 1,
        border: `1.5px solid ${selected ? "var(--accent)" : "var(--rule)"}`,
      }}
    >
      {selected ? "✓" : ""}
    </span>
  );
}

function QuestionCard({
  q,
  selected,
  onSelect,
}: {
  q: QuestionMenuItem;
  selected: boolean;
  onSelect: () => void;
}) {
  const [hover, setHover] = useState(false);
  // Border colours follow the design: hairline at rest, ink on hover, and
  // an accent left-bar (thicker) when selected.
  const edge = selected || hover ? "var(--text)" : "var(--hair)";
  const leftEdge = selected ? "var(--accent)" : hover ? "var(--text)" : "var(--hair)";
  return (
    <button
      type="button"
      data-testid={`question-option-${q.slug}`}
      aria-pressed={selected}
      onClick={onSelect}
      onMouseEnter={() => setHover(true)}
      onMouseLeave={() => setHover(false)}
      className={cn(
        "text-left cursor-pointer font-sans text-text flex flex-col gap-2.5 transition-colors",
        selected ? "bg-surface-alt" : "bg-surface",
      )}
      style={{
        padding: "20px 22px",
        border: `1.5px solid ${edge}`,
        borderLeft: `${selected ? 3 : 1.5}px solid ${leftEdge}`,
      }}
    >
      <div className="flex justify-between items-start gap-4">
        <div className="flex items-start gap-[14px]">
          <CaseRadio selected={selected} />
          <span className="text-[16px] font-bold tracking-[-0.02em] leading-[1.18]">
            {q.display_name}
          </span>
        </div>
        <div className="text-right flex-shrink-0">
          <div
            className="text-[21px] font-extrabold leading-none"
            style={{ letterSpacing: "-0.03em" }}
          >
            {formatCurrency(q.price_cents, q.currency)}
          </div>
          <Mono muted size={8.5} className="block mt-[3px]">
            PER ANSWER
          </Mono>
        </div>
      </div>
      <p className="text-[13px] leading-[1.5] text-text-muted m-0" style={{ paddingLeft: 30 }}>
        {q.summary}
      </p>
    </button>
  );
}
