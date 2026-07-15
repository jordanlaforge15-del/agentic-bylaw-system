// Shared TypeScript shapes for the case-credit billing endpoints.
// One file because both server-side proxy routes and client-side
// components need the same shapes — keeping them in one place means
// renaming a field is one edit.

import type { ReportContent } from "@/lib/report";

export type Tier = "quick" | "standard" | "complex";
export type PackSku = "payg" | "starter" | "pro" | "enterprise";
export type CaseStatus = "open" | "closed" | "archived";
export type AnchorKind =
  | "address"
  | "project_ref"
  | "development_application";

export type CatalogOffer = {
  tier: Tier;
  tier_display_name: string;
  tier_token_budget: number;
  pack_sku: PackSku;
  pack_display_name: string;
  quantity: number;
  discount_bps: number;
  list_price_cents: number;
  amount_due_cents: number;
  currency: string;
  available: boolean;
};

export type CatalogResponse = {
  enabled: boolean;
  currency: string;
  cad_per_usd: number;
  offers: CatalogOffer[];
};

export type TierBalance = {
  tier: Tier;
  available: number;
  reserved: number;
  consumed: number;
};

export type BillingMeResponse = {
  enabled: boolean;
  stripe_customer_id: string | null;
  tier_balances: TierBalance[];
  total_available_credits: number;
  free_questions_remaining: number;
};

export type PurchaseSummary = {
  id: number;
  tier: Tier;
  pack_sku: PackSku;
  quantity: number;
  amount_paid_cents: number;
  currency: string;
  created_at: string;
};

export type PurchaseHistoryResponse = {
  purchases: PurchaseSummary[];
};

export type CaseRow = {
  id: number;
  user_case_number: number;
  user_id: number;
  anchor_label: string;
  anchor_kind: AnchorKind;
  status: CaseStatus;
  current_tier: Tier | null;
  tokens_consumed: number;
  opened_at: string;
  last_activity_at: string;
  closed_at: string | null;
};

export type CaseListResponse = {
  cases: CaseRow[];
};

export type MatchResponse = {
  matched: boolean;
  case: CaseRow | null;
};

export type ClassifyResponse = {
  tier: Tier;
  confidence: number;
  reasons: string[];
};

export type OpenCaseResponse = {
  case: CaseRow;
  // ABS-382: opening a case is free — no CaseCredit is reserved, so
  // `credit_id` is always null now (kept for old-frontend compat).
  credit_id: number | null;
  reused_existing_case: boolean;
};

export type UpgradeResponse = {
  case: CaseRow;
  new_credit_id: number;
  burned_credit_id: number;
};

export const TIER_DISPLAY: Record<Tier, string> = {
  quick: "Quick Lookup",
  standard: "Standard Case",
  complex: "Complex File",
};

export type QuestionInputField = {
  name: string;
  label: string;
  required: boolean;
  description: string;
};

export type QuestionMenuItem = {
  slug: string;
  display_name: string;
  price_cents: number;
  currency: string;
  summary: string;
  backing_calls: string[];
  required_inputs: QuestionInputField[];
  catalog_anchor: string;
  available: boolean;
};

export type QuestionMenuResponse = {
  enabled: boolean;
  // ABS-322/348: whether an answer is unlocked by a real Stripe payment
  // (true) or a free-question credit (false). Payments-off is the go-live
  // posture — `available` questions unlock via a credit, not a paid
  // checkout. The entry flow branches its CTA + completion routing on this.
  payments_enabled?: boolean;
  // ABS-324 launch posture: when false, /cases/new is Answers-only — the
  // continue-existing-case (Conversation /app chat) entry is hidden.
  conversation_enabled?: boolean;
  currency: string;
  cad_per_usd: number;
  questions: QuestionMenuItem[];
};

// ABS-312/321 — state of a priced-question purchase + its raw answer.
// Mirrors advisor.billing.router.QuestionPurchaseResponse. `answer` is
// null until the engine produces a grounded result (status "captured");
// on a failed question (status "voided"/"failed") it stays null and
// `failure_reason` carries the machine code ("zero_evidence",
// "cost_ceiling", "internal_error").
export type QuestionPurchaseResponse = {
  id: number;
  question_slug: string;
  status: string;
  price_cents: number;
  currency: string;
  answer: string | null;
  // ABS-342: the structured report deliverable, present only on a
  // `captured` purchase. The answer-view renders this through the shared
  // ReportDocument template; `answer` (raw markdown) is the defensive
  // fallback when it's absent.
  report: ReportContent | null;
  failure_reason: string | null;
  refinement_count: number;
  refinements_remaining: number;
  window_expires_at: string | null;
};

// Turn a catalog slug ("permitted_use") into a human title
// ("Permitted use") for the answer-view header when the full catalog
// display_name isn't in hand. "other" is the off-menu free-form sentinel.
export function humanizeQuestionSlug(slug: string): string {
  if (slug === "other") return "Custom question";
  return slug
    .split("_")
    .map((w) => (w ? w[0].toUpperCase() + w.slice(1) : w))
    .join(" ");
}
// Consultant-style intake (ABS-315). One pass over the user's free-form
// description: the backend extracts whatever inputs it can and reports
// whether the question's required-input schema is satisfied. Always free.
export type IntakeResponse = {
  question_slug: string;
  complete: boolean;
  inputs: Record<string, string>;
  missing_required: string[];
  missing_optional: string[];
  prompt: string;
};

// Checkout for a catalog question (POST /api/billing/checkout/question).
export type QuestionCheckoutResponse = {
  url: string;
  purchase_id: number;
};

// Free off-menu price quote (ABS-316). Producing it never charges.
export type QuoteResponse = {
  question: string;
  difficulty: string;
  difficulty_display_name: string;
  price_cents: number;
  currency: string;
  rationale: string;
  band_low_cents: number;
  band_high_cents: number;
};

// Free-question trial start (POST /api/billing/questions/free-start).
// Payments-off (ABS-322), decoupled by ABS-324: consumes one free
// entitlement and opens an Answers `QuestionPurchase` (NOT a Case),
// returning the purchase_id so the browser opens it in the unified /app
// workspace (/app?report_id={id}, ABS-344).
export type FreeStartResponse = {
  purchase_id: number;
  status: string;
  free_questions_remaining: number;
};

// Checkout for an off-menu "Other" question (POST /api/billing/checkout/other).
export type OtherCheckoutResponse = {
  url: string;
  purchase_id: number;
  price_cents: number;
  currency: string;
  difficulty: string;
  rationale: string;
};

export const ANCHOR_KIND_DISPLAY: Record<AnchorKind, string> = {
  address: "Property address",
  project_ref: "Project reference",
  development_application: "Development application",
};

export function formatCurrency(
  cents: number,
  currency: string = "CAD",
): string {
  // Use Intl.NumberFormat with the chosen currency. The catalog ships
  // CAD; the FX-displayed USD on the marketing page uses a different
  // formatter (see formatUsdFromCadCents).
  const dollars = cents / 100;
  return new Intl.NumberFormat("en-CA", {
    style: "currency",
    currency,
    minimumFractionDigits: dollars % 1 === 0 ? 0 : 2,
    maximumFractionDigits: 2,
  }).format(dollars);
}

export function formatUsdFromCadCents(
  cents_cad: number,
  cad_per_usd: number,
): string {
  if (cad_per_usd <= 0) return "";
  const usd = cents_cad / 100 / cad_per_usd;
  return new Intl.NumberFormat("en-US", {
    style: "currency",
    currency: "USD",
    minimumFractionDigits: 0,
    maximumFractionDigits: 2,
  }).format(usd);
}

export function formatDiscount(bps: number): string {
  if (bps <= 0) return "";
  return `${bps / 100}% off`;
}

export function formatTokenBudget(tokens: number): string {
  if (tokens >= 1000) return `${Math.round(tokens / 1000)}k tokens`;
  return `${tokens} tokens`;
}
