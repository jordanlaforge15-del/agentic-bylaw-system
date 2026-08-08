// Token-wallet / turns parameters, mirrored from src/advisor/billing/turns.py
// (ABS-416). Single source of truth for the suite: before this existed, five
// specs each redeclared `SIGNUP_GRANT = 25_000` / `TOKENS_PER_TURN = 2_500`,
// so recalibrating the conversion rate meant hunting literals across the
// suite and any one that was missed failed for a reason unrelated to its
// subject.
//
// These are the *code defaults*. The e2e stack does not override the
// ADVISOR_* wallet env vars, so the defaults are authoritative there — and
// `abs416-turn-calibration.spec.ts` asserts the live backend agrees, which is
// what makes it safe for other specs to import these as constants.

/** ADVISOR_TOKENS_PER_TURN — the backend-owned display divisor. */
export const TOKENS_PER_TURN = 175_000;

/** ADVISOR_SIGNUP_TOKEN_GRANT — one-time grant on first sign-in. */
export const SIGNUP_GRANT = 1_750_000;

/** ADVISOR_LOW_BALANCE_WARN_TOKENS — `low_balance` flips at <= this. */
export const LOW_BALANCE_WARN = 350_000;

/** ADVISOR_CHAT_MIN_BALANCE_TOKENS — pre-flight refuses at <= this. */
export const CHAT_MIN_BALANCE = 0;

/** Turns a brand-new wallet is worth: floor(grant / per-turn) == 10. */
export const SIGNUP_GRANT_TURNS = Math.floor(SIGNUP_GRANT / TOKENS_PER_TURN);

/** Turn counts the paid top-up SKUs advertise, cheapest-first. */
export const TOPUP_TURNS: Record<string, number> = {
  small: 8,
  medium: 30,
  large: 80,
};

/**
 * Convert turns to tokens for stubbed wallet payloads, so a stub's
 * `balance_tokens` and `approx_turns_remaining` stay mutually consistent
 * instead of drifting into a combination the backend could never emit.
 */
export function turnsToTokens(turns: number): number {
  return turns * TOKENS_PER_TURN;
}
