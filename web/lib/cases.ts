// Shared TypeScript shapes for the case-credit billing endpoints.
// One file because both server-side proxy routes and client-side
// components need the same shapes — keeping them in one place means
// renaming a field is one edit.

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
  credit_id: number;
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
