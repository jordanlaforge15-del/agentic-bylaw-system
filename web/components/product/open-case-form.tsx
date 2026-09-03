// Conversation-first /cases/new (ABS-385 · beta pivot I6). Opening a case
// is FREE and starts a conversation — the user anchors to a property, asks
// their question, and clicks "Start the conversation — free →". A case is
// created via POST /api/cases (no purchase, no credit) and the browser
// lands on /app?case_id=N with the question auto-sent via ?first_message=.
//
// Chat is billed by the account token wallet (ABS-380/383), shown near the
// CTA as a BalanceChip ("~N turns"). Released report SKUs (per-report gates,
// ABS-384) render as a SECONDARY, collapsed ReportOffer accordion; when no
// slug is enabled the report section does not render at all and the page
// reads complete as anchor + question + free CTA.
//
// The report accordion preserves the priced-question test contract from the
// prior surface (data-testids `question-menu`, `question-option-${slug}`,
// `free-trial-btn`, `free-trial-exhausted`, `intake-prompt`; the "Describe
// your situation" label and "Tell us what you want answered" placeholder;
// intake -> checkout / free-start routing) — it is the same Answers flow,
// now demoted behind an accordion instead of the primary entry.

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
  OpenCaseResponse,
  QuestionCheckoutResponse,
  QuestionMenuItem,
  QuestionMenuResponse,
  WalletResponse,
  formatCurrency,
} from "@/lib/cases";
import { Btn } from "@/components/btn";
import { Mono } from "@/components/mono";
import {
  BetaRefillClaim,
  canClaimRefill,
  refillTurnsLabel,
  refillUnlockLabel,
} from "@/components/product/beta-refill";
import { cn } from "@/lib/cn";
import { TURN_APPROX_SHORT } from "@/lib/turn-copy";

// Only "address" is backed by a live data source in beta (see ABS-200).
const ANCHOR_KIND_OPTIONS: AnchorKind[] = ["address"];

type Working = "idle" | "intake" | "checkout";

// ABS-450: opening a case needs both an anchor and a first message. Returns
// the guidance line naming whatever is still empty, or null once the form is
// complete. Drives both the CTA's disabled state and the visible hint, so the
// button never sits enabled-looking over a no-op click.
function missingFieldsMessage(
  anchorLabel: string,
  question: string,
): string | null {
  const noAnchor = !anchorLabel.trim();
  const noQuestion = !question.trim();
  if (noAnchor && noQuestion)
    return "Add a property address and your question to start.";
  if (noAnchor) return "Add a property address to start.";
  if (noQuestion) return "Add your question to start.";
  return null;
}

export function OpenCaseForm({
  initialAnchorLabel = "",
  initialMessage = "",
  initialReportSlug = "",
}: {
  initialAnchorLabel?: string;
  initialMessage?: string;
  initialReportSlug?: string;
}) {
  const router = useRouter();
  const [anchorLabel, setAnchorLabel] = useState(initialAnchorLabel);
  const [anchorKind, setAnchorKind] = useState<AnchorKind>("address");
  const [match, setMatch] = useState<CaseRow | null | undefined>(undefined);

  // The conversation's first message. Auto-sent on /app via ?first_message=.
  const [question, setQuestion] = useState(initialMessage);

  const [wallet, setWallet] = useState<WalletResponse | null>(null);

  const [menu, setMenu] = useState<QuestionMenuResponse | null>(null);
  const [menuError, setMenuError] = useState(false);

  // The report accordion: at most one offer is expanded at a time. A
  // `?report=<slug>` deep-link pre-expands that offer (nothing is selected
  // otherwise). The intake state below belongs to whichever offer is open.
  const [expandedSlug, setExpandedSlug] = useState<string | null>(
    initialReportSlug || null,
  );
  const [reportConversation, setReportConversation] = useState("");
  const [collectedInputs, setCollectedInputs] = useState<
    Record<string, string>
  >({});
  const [intake, setIntake] = useState<IntakeResponse | null>(null);

  const [freeQuestionsRemaining, setFreeQuestionsRemaining] = useState<
    number | null
  >(null);

  const [opening, setOpening] = useState(false);
  const [working, setWorking] = useState<Working>("idle");
  const [error, setError] = useState<string | null>(null);

  // Load the account token wallet (ABS-380). 401 = not signed in; stay null
  // and the BalanceChip simply doesn't render — the free CTA still works.
  useEffect(() => {
    let cancelled = false;
    fetch("/api/billing/wallet")
      .then((r) => (r.ok ? r.json() : Promise.reject(r.status)))
      .then((data: WalletResponse) => {
        if (!cancelled) setWallet(data);
      })
      .catch(() => {
        /* unauthenticated or unavailable — leave null */
      });
    return () => {
      cancelled = true;
    };
  }, []);

  // Load the released report SKUs. Per-report gates (ABS-384) mean the menu
  // already carries only enabled slugs; an empty list => no report section.
  useEffect(() => {
    let cancelled = false;
    fetch("/api/billing/questions")
      .then((r) => (r.ok ? r.json() : Promise.reject(r.status)))
      .then((data: QuestionMenuResponse) => {
        if (!cancelled) setMenu(data);
      })
      .catch(() => {
        if (!cancelled) setMenuError(true);
      });
    return () => {
      cancelled = true;
    };
  }, []);

  // Load the user's free-question counter (report free-trial gating). 401 =
  // not signed in; stay null.
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

  const expandedQuestion: QuestionMenuItem | undefined = useMemo(
    () => menu?.questions.find((q) => q.slug === expandedSlug),
    [menu, expandedSlug],
  );

  // ABS-348: does unlocking a report cost real money (Stripe) or a free
  // credit? Payments-off is the go-live posture — an `available` question
  // unlocks by consuming a free-question credit, not a paid checkout.
  const paymentsEnabled = menu?.payments_enabled ?? false;

  function unauthorized() {
    router.push("/login?next=/cases/new");
  }

  // ABS-385: the always-on in-window match lookup. No conversation_enabled
  // gate — the conversation product is the primary surface now.
  async function lookupMatch() {
    if (!anchorLabel.trim()) return;
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
    }
  }

  // The free entry point: open a case (POST /api/cases — no purchase, no
  // credit) and land on /app with the question auto-sent. Idempotent on the
  // anchor server-side, so re-opening the same address reuses the case.
  async function startConversation() {
    // ABS-450: the CTA is disabled until both fields carry content, so this
    // is the belt-and-braces path (keyboard/programmatic submits). It must
    // still say *why* nothing happened rather than returning silently.
    if (!anchorLabel.trim() || !question.trim()) {
      setError(missingFieldsMessage(anchorLabel, question) ?? null);
      return;
    }
    setOpening(true);
    setError(null);
    try {
      let r: Response;
      try {
        r = await fetch("/api/cases", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
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
      if (!r.ok) {
        const detail = await r.json().catch(() => null);
        setError(
          (detail?.detail as { message?: string } | undefined)?.message ??
            `Couldn't open the case (${r.status}).`,
        );
        return;
      }
      const data = (await r.json()) as OpenCaseResponse;
      const params = new URLSearchParams({ case_id: String(data.case.id) });
      const firstMessage = question.trim();
      if (firstMessage) params.set("first_message", firstMessage);
      router.push(`/app?${params.toString()}`);
    } finally {
      setOpening(false);
    }
  }

  function toggleOffer(slug: string) {
    setExpandedSlug((prev) => (prev === slug ? null : slug));
    setIntake(null);
    setCollectedInputs({});
    setReportConversation("");
    setError(null);
  }

  // Payments-off path (ABS-322/324): consume one free-question entitlement
  // to open a report purchase, then route to the answer view.
  async function startFreeAnswer(
    questionSlug: string,
    inputs: Record<string, string>,
  ) {
    if (!anchorLabel.trim()) {
      setError("Add a property address before ordering a report.");
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
            `Couldn't start the report (${r.status}).`,
        );
        return;
      }
      const data = (await r.json()) as FreeStartResponse;
      setFreeQuestionsRemaining(data.free_questions_remaining);
      router.push(`/app?report_id=${data.purchase_id}`);
    } finally {
      setWorking((w) => (w === "checkout" ? "idle" : w));
    }
  }

  // Report question: run one intake pass. The anchor address is seeded as a
  // collected input so an address-bearing question never re-asks for it.
  async function runIntake(q: QuestionMenuItem) {
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
            question_slug: q.slug,
            conversation: reportConversation,
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
      if (data.complete) {
        // ABS-348: route on payments, not the billing master switch. Only a
        // payments-on, available question goes to Stripe checkout; everything
        // else (dormant billing, OR billing-on/payments-off go-live) unlocks
        // via a free credit and lands on the answer view.
        const paidCheckout = q.available === true && paymentsEnabled;
        if (paidCheckout) {
          await checkoutQuestion(q.slug, data.inputs);
        } else {
          await startFreeAnswer(q.slug, data.inputs);
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
      // and a null URL. Route into the unified /app workspace instead of
      // erroring on the missing checkout URL.
      if (!data.url && data.purchase_id) {
        router.push(`/app?report_id=${data.purchase_id}`);
        return;
      }
      if (data.url) {
        window.location.href = data.url;
        return;
      }
      setError("Checkout returned no URL. Try again.");
    } finally {
      setWorking((w) => (w === "checkout" ? "idle" : w));
    }
  }

  const busy = working !== "idle";
  const reportCount = menu?.questions.length ?? 0;
  const missing = missingFieldsMessage(anchorLabel, question);

  return (
    <div className="flex flex-col">
      {/* Anchor */}
      <section className="mb-9">
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
        <span className="block text-[13px] text-text-muted leading-[1.45] mt-2.5">
          Anchor to a property so the assistant can pull the right by-laws.
          Starting the conversation is free.
        </span>
      </section>

      {/* Always-on in-window match (ABS-385: no conversation_enabled gate). */}
      {match !== undefined && match !== null && (
        <section className="mb-9">
          <div
            role="note"
            className="bg-surface-alt border border-hair p-4"
            data-testid="existing-case-match"
          >
            <Mono size={11} muted>
              EXISTING CASE FOUND
            </Mono>
            <div className="mt-1 text-[13.5px]">
              You have a conversation for this address.
            </div>
            <div className="mt-3">
              <Btn
                variant="primary"
                size="sm"
                onClick={() => router.push(`/app?case_id=${match.id}`)}
              >
                Continue conversation →
              </Btn>
            </div>
          </div>
        </section>
      )}

      {/* Your question — the conversation's first message. */}
      <section className="mb-9">
        <Mono muted size={10} className="block mb-3.5">
          YOUR QUESTION
        </Mono>
        <textarea
          aria-label="Your question"
          value={question}
          onChange={(e) => {
            setQuestion(e.target.value);
            setError(null);
          }}
          placeholder="Ask your question. e.g. Can I add a basement apartment at this address?"
          className="w-full box-border border-[1.5px] border-text bg-surface text-text font-sans text-[14px] outline-none resize-y leading-[1.55]"
          style={{ padding: "14px 16px", minHeight: 120 }}
        />
      </section>

      {/* Free CTA + BalanceChip + brick attention notice. */}
      <section
        className="flex flex-col gap-4"
        style={{ borderTop: "1px solid var(--rule)", paddingTop: 22 }}
      >
        <div className="flex justify-between items-end gap-7 flex-wrap">
          <BalanceChip wallet={wallet} />
          <div className="flex flex-col items-end gap-2">
            <Btn
              variant="accent"
              size="lg"
              onClick={startConversation}
              disabled={opening || missing !== null}
              aria-describedby={missing ? "start-conversation-hint" : undefined}
              data-testid="start-conversation-btn"
              className="whitespace-nowrap"
            >
              {opening ? "Opening…" : "Start the conversation — free →"}
            </Btn>
            {/* ABS-450: say what's still missing instead of leaving a dead
                CTA. Present from first paint, so an empty-form click is
                never a silent no-op. */}
            {missing && (
              <span
                id="start-conversation-hint"
                data-testid="start-conversation-hint"
                className="text-[12.5px] text-text-muted text-right"
              >
                {missing}
              </span>
            )}
          </div>
        </div>
        <BalanceNotice wallet={wallet} onRefilled={setWallet} />
      </section>

      {/* Secondary: released report SKUs. Rendered only when ≥1 slug is
          enabled — a zero-slug gate leaves the page as anchor + question +
          free CTA (ABS-384/385). */}
      {menuError ? null : reportCount > 0 && menu ? (
        <section className="mt-12">
          <Mono muted size={10} className="block mb-3.5">
            OR ORDER A WRITTEN REPORT · {reportCount}
          </Mono>
          <div className="flex flex-col gap-3" data-testid="question-menu">
            {menu.questions.map((q) => (
              <ReportOffer
                key={q.slug}
                q={q}
                expanded={expandedSlug === q.slug}
                onToggle={() => toggleOffer(q.slug)}
                paymentsEnabled={paymentsEnabled}
                freeQuestionsRemaining={freeQuestionsRemaining}
                reportConversation={reportConversation}
                setReportConversation={setReportConversation}
                intake={intake}
                working={working}
                busy={busy}
                onRunIntake={() => runIntake(q)}
              />
            ))}
          </div>
        </section>
      ) : null}

      {error && (
        <div className="mt-4 text-[13px] text-brick" data-testid="open-case-error">
          {error}
        </div>
      )}

      <Mono muted size={9} className="block mt-9">
        NOT LEGAL ADVICE · RESEARCH ONLY · VERIFY WITH HRM PLANNING
      </Mono>
    </div>
  );
}

// Balance chip shown near the CTA. Speaks in turns, never tokens (design D7).
// Healthy = accent dot + "~N turns left" (payments-off: "~N free trial
// turns"); low/empty = brick border + alarm glyph; empty label is "0 turns".
// aria-label expands the "~" to "approximately" for screen readers.
//
// ABS-452: a non-zero figure here is the first turn count a user ever sees, and
// it reads as a hard count ("I have 150 turns") unless we say otherwise — one
// multi-attribute evaluation can cost ~100 turns' worth of tokens. The standard
// approximate disclosure rides under the chip, and on the aria-label + title so
// it reaches assistive tech and hover. Suppressed at zero, where "0 turns" has
// no variance to caveat.
function BalanceChip({ wallet }: { wallet: WalletResponse | null }) {
  if (!wallet) return null;
  const turns = Math.max(0, wallet.approx_turns_remaining);
  const empty = turns <= 0;
  const low = wallet.low_balance && !empty;
  const attention = empty || low;
  const paid = wallet.payments_enabled;

  const label = empty
    ? "0 turns"
    : paid
      ? `~${turns} turns left`
      : `~${turns} free trial turns`;
  const baseAriaLabel = empty
    ? "0 turns"
    : paid
      ? `approximately ${turns} turns left`
      : `approximately ${turns} free trial turns`;
  const ariaLabel = empty
    ? baseAriaLabel
    : `${baseAriaLabel}. ${TURN_APPROX_SHORT}.`;

  return (
    <div className="flex flex-col gap-1.5" data-testid="balance-chip">
      <span
        role="img"
        aria-label={ariaLabel}
        title={empty ? undefined : TURN_APPROX_SHORT}
        className={cn(
          "inline-flex items-center gap-2 self-start",
          attention ? "border-[1.5px] border-brick" : "border border-hair",
        )}
        style={{ padding: "5px 10px" }}
      >
        {attention ? (
          <span
            aria-hidden
            data-testid="balance-alarm"
            className="text-brick font-bold leading-none"
            style={{ fontSize: 13 }}
          >
            ⚠
          </span>
        ) : (
          <span
            aria-hidden
            className="bg-accent"
            style={{ width: 6, height: 6 }}
          />
        )}
        <span
          className={cn(
            "text-[13px] font-semibold tracking-[-0.01em]",
            attention ? "text-brick" : "text-text",
          )}
        >
          {label}
        </span>
      </span>
      <Mono muted size={9}>
        OPENING IS FREE
      </Mono>
      {!empty && (
        <span
          data-testid="balance-chip-approx-note"
          className="text-text-muted text-[11px] leading-[1.45] max-w-[34ch]"
        >
          {TURN_APPROX_SHORT}
        </span>
      )}
    </div>
  );
}

// Brick attention notice (role=note), rendered only when the wallet is low
// or empty. Copy is exact per the design package, forked on payments-on/off
// and low/empty. The free CTA still works in every state — this is guidance,
// not a gate.
function BalanceNotice({
  wallet,
  onRefilled,
}: {
  wallet: WalletResponse | null;
  onRefilled: (wallet: WalletResponse) => void;
}) {
  if (!wallet) return null;
  const turns = Math.max(0, wallet.approx_turns_remaining);
  const empty = turns <= 0;
  const low = wallet.low_balance && !empty;
  if (!empty && !low) return null;
  const paid = wallet.payments_enabled;
  // ABS-405: an empty wallet on the payments-off beta is the state this
  // page used to leave as a flat "open it anyway and see a dead composer".
  // Offer the same self-serve claim the chat prompt does — this is the
  // primary entry flow, so a fix that skipped it would still strand the
  // user one screen later.
  const refill = canClaimRefill(wallet) ? wallet.beta_refill : undefined;
  const cooldownUntil =
    !paid && wallet.beta_refill?.status === "cooldown"
      ? refillUnlockLabel(wallet.beta_refill.next_available_at)
      : null;

  let body: string;
  if (empty && paid) {
    body =
      "Opening is free, but replies need turns and you're out. Top up now or after you open.";
  } else if (empty && !paid && refill) {
    body =
      "Opening is free, but replies need turns and your free trial is used up. " +
      `You can add ${refillTurnsLabel(refill)} to this account right now.`;
  } else if (empty && !paid) {
    body =
      "Opening is free, but replies need turns and your free trial is used up. Paid top-ups open soon." +
      (cooldownUntil ? ` More turns unlock ${cooldownUntil}.` : "");
  } else if (low && paid) {
    body =
      "You're running low. Opening is free; your first reply will draw on your last turns.";
  } else {
    body = "You're running low on free trial turns. Opening is still free.";
  }

  return (
    <div
      role="note"
      data-testid="balance-notice"
      className="border-[1.5px] border-brick bg-surface-alt p-4 flex flex-col gap-3 items-start"
    >
      <div className="text-[13.5px] leading-[1.5] text-text">{body}</div>
      {paid && (
        <Btn
          variant="ghost"
          size="sm"
          onClick={() => {
            window.location.href = "/app/billing";
          }}
          data-testid="top-up-turns-btn"
          className="whitespace-nowrap"
        >
          Top up turns →
        </Btn>
      )}
      {empty && refill && <BetaRefillClaim onRefilled={onRefilled} />}
    </div>
  );
}

// A collapsed report offer. Expanding it reveals the intake textarea + the
// unlock CTA (free-trial / paid checkout / exhausted notice), preserving the
// prior Answers flow behind the accordion.
function ReportOffer({
  q,
  expanded,
  onToggle,
  paymentsEnabled,
  freeQuestionsRemaining,
  reportConversation,
  setReportConversation,
  intake,
  working,
  busy,
  onRunIntake,
}: {
  q: QuestionMenuItem;
  expanded: boolean;
  onToggle: () => void;
  paymentsEnabled: boolean;
  freeQuestionsRemaining: number | null;
  reportConversation: string;
  setReportConversation: (v: string) => void;
  intake: IntakeResponse | null;
  working: Working;
  busy: boolean;
  onRunIntake: () => void;
}) {
  // ABS-348: a *paid* purchase requires payments ON. Payments-off => an
  // `available` question is a free-credit unlock (free-trial affordance, or
  // the exhausted notice once credits are spent), not a "$79" checkout.
  const canPurchase = q.available === true && paymentsEnabled;
  const canFreeTrial =
    !canPurchase &&
    freeQuestionsRemaining !== null &&
    freeQuestionsRemaining > 0;
  const trialExhausted = !canPurchase && freeQuestionsRemaining === 0;

  const ctaLabel =
    working === "intake"
      ? "Checking…"
      : working === "checkout"
        ? paymentsEnabled
          ? "Opening checkout…"
          : "Starting…"
        : canPurchase
          ? `Get report · ${formatCurrency(q.price_cents, q.currency)}`
          : "Start report (free trial) →";

  return (
    <div
      className={cn(
        "border-[1.5px]",
        expanded ? "border-text bg-surface-alt" : "border-hair bg-surface",
      )}
    >
      <button
        type="button"
        data-testid={`question-option-${q.slug}`}
        aria-expanded={expanded}
        onClick={onToggle}
        className="w-full text-left cursor-pointer font-sans text-text"
        style={{ padding: "18px 20px" }}
      >
        <div className="flex justify-between items-start gap-4">
          <div className="flex items-start gap-[14px]">
            <OfferIndicator expanded={expanded} />
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
              PER REPORT
            </Mono>
          </div>
        </div>
      </button>

      {expanded && (
        <div style={{ padding: "0 20px 20px" }}>
          <p className="text-[13px] leading-[1.5] text-text-muted m-0 mb-4">
            {q.summary}
          </p>

          <Mono muted size={10} className="block mb-3.5">
            DESCRIBE YOUR SITUATION
          </Mono>
          <textarea
            aria-label="Describe your situation"
            value={reportConversation}
            onChange={(e) => setReportConversation(e.target.value)}
            placeholder="Tell us what you want answered. We'll ask for anything else we need before you pay."
            className="w-full box-border border-[1.5px] border-text bg-surface text-text font-sans text-[14px] outline-none resize-y leading-[1.55]"
            style={{ padding: "14px 16px", minHeight: 120 }}
          />

          {intake && !intake.complete && intake.prompt && (
            <div
              className="bg-surface border border-hair p-4 mt-2.5"
              data-testid="intake-prompt"
            >
              <Mono size={11} muted>
                ONE MORE THING
              </Mono>
              <div className="mt-1 text-[13.5px]">{intake.prompt}</div>
            </div>
          )}

          <div className="mt-4 flex items-center gap-4 flex-wrap">
            {canPurchase ? (
              <Btn
                variant="accent"
                size="md"
                onClick={onRunIntake}
                disabled={busy}
                className="whitespace-nowrap"
              >
                {ctaLabel}
              </Btn>
            ) : canFreeTrial ? (
              <Btn
                variant="accent"
                size="md"
                onClick={onRunIntake}
                disabled={busy}
                data-testid="free-trial-btn"
                className="whitespace-nowrap"
              >
                {ctaLabel}
              </Btn>
            ) : trialExhausted ? (
              <div
                className="text-[12.5px] text-text-muted max-w-[260px]"
                data-testid="free-trial-exhausted"
              >
                Free trial used — paid answers coming soon.
              </div>
            ) : (
              <div className="text-[12.5px] text-text-muted max-w-[260px]">
                Checkout isn&apos;t configured for this question yet.
              </div>
            )}
          </div>
        </div>
      )}
    </div>
  );
}

// A 16px square indicator: hairline when collapsed, accent-filled with a
// caret when expanded. Mirrors the CaseRadio affordance the margin spec
// (ABS-337) measures against the title.
function OfferIndicator({ expanded }: { expanded: boolean }) {
  return (
    <span
      aria-hidden
      className={cn(
        "inline-flex items-center justify-center flex-shrink-0 font-mono",
        expanded ? "bg-accent text-on-accent" : "bg-transparent",
      )}
      style={{
        width: 16,
        height: 16,
        marginTop: 1,
        fontSize: 10,
        lineHeight: 1,
        border: `1.5px solid ${expanded ? "var(--accent)" : "var(--rule)"}`,
      }}
    >
      {expanded ? "–" : "+"}
    </span>
  );
}
