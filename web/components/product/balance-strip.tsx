// In-chat strip that surfaces the account token wallet as *turns* (ABS-386).
// Replaces the retired tier/budget CaseHeaderStrip: the beta pivot bills chat
// by an account-level token wallet, and every balance-bearing figure is shown
// as "~N turns" — never raw token counts, never tier vocabulary.
//
// Sits between the chat thread and the composer so it's visible without
// stealing focus. Seeded from GET /api/billing/wallet on mount and updated
// live off the per-turn `token_balance` SSE event (approx_turns_remaining /
// low_balance), so it decrements without a reload.
//
// Attention states follow the design package's global rules:
//   * `~` prefix + the standard disclosure ("Counts are approximate — complex
//     questions use more") — turns are an estimate; complex questions burn
//     more per turn.
//   * LOW treatment uses the BRICK attention colour + an alarm glyph, never
//     colour alone (the glyph + text carry the meaning). The lime ACCENT is
//     reserved for positive affordances and is deliberately not used here.
//   * aria-live="polite" so the live decrement is announced, and the turns
//     label expands `~` to "approximately" for screen readers.

"use client";

import { Mono } from "@/components/mono";

type Props = {
  caseId: number | null;
  caseNumber: number | null;
  /** Backend-owned turns conversion. Null while the wallet seed is in flight. */
  approxTurnsRemaining: number | null;
  lowBalance: boolean;
  /** Whether purchasing is live. The "Top up →" nudge only shows when true. */
  paymentsEnabled: boolean;
};

const APPROX_DISCLOSURE = "Counts are approximate — complex questions use more";

export function BalanceStrip({
  caseId,
  caseNumber,
  approxTurnsRemaining,
  lowBalance,
  paymentsEnabled,
}: Props) {
  // Purely informational; the strip only applies to an active (billed) case.
  if (caseId === null) return null;
  const turns = approxTurnsRemaining ?? 0;
  const turnWord = turns === 1 ? "turn" : "turns";

  return (
    <div
      data-testid="balance-strip"
      role="status"
      aria-live="polite"
      className="border-t border-hair px-4 py-2 bg-surface-alt flex items-center gap-2.5 text-[12px]"
    >
      <Mono muted size={10.5}>
        Case #{caseNumber ?? caseId}
      </Mono>
      <span aria-hidden className="text-text-muted">
        ·
      </span>
      {lowBalance && (
        <span
          data-testid="balance-low-glyph"
          aria-hidden
          className="text-brick font-bold leading-none"
        >
          ▲
        </span>
      )}
      <span
        data-testid="balance-turns"
        className={lowBalance ? "text-brick font-semibold" : "font-semibold"}
        // Expand the `~` to "approximately" for assistive tech; the disclosure
        // rides along as the accessible/hover explanation.
        aria-label={`approximately ${turns} ${turnWord} left. ${APPROX_DISCLOSURE}.`}
        title={APPROX_DISCLOSURE}
      >
        ~{turns} {turnWord} left
      </span>
      {/* Standard disclosure, visible on wider screens; the `~` + title carry
       * it on mobile where horizontal room is tight. */}
      <span
        data-testid="balance-approx-note"
        className="hidden md:inline text-text-muted text-[11px]"
      >
        {APPROX_DISCLOSURE}
      </span>
      {lowBalance && paymentsEnabled && (
        <a
          href="/pricing"
          data-testid="balance-topup-link"
          className="ml-auto underline text-text whitespace-nowrap"
        >
          Top up →
        </a>
      )}
    </div>
  );
}
