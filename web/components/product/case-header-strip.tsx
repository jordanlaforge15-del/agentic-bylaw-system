// In-chat strip that surfaces the active case + tier and any
// budget-warning payload from the backend. Sits between the chat
// thread and the composer so it's visible without stealing focus.
//
// We render NOTHING when there's no caseId — the strip is purely
// informational and doesn't apply to the legacy non-billed chat path.

"use client";

type Props = {
  caseId: number | null;
  tier: string | null;
  budgetWarning: {
    case_id: number;
    tier: string;
    remaining_tokens: number;
    tier_budget: number;
  } | null;
};

const TIER_DISPLAY: Record<string, string> = {
  quick: "Quick Lookup",
  standard: "Standard Case",
  complex: "Complex File",
};


export function CaseHeaderStrip({ caseId, tier, budgetWarning }: Props) {
  if (caseId === null && !tier) return null;
  const showWarning =
    budgetWarning !== null &&
    budgetWarning.tier_budget > 0 &&
    budgetWarning.case_id === caseId;

  const fraction = showWarning
    ? Math.max(
        0,
        Math.min(
          1,
          budgetWarning.remaining_tokens / budgetWarning.tier_budget,
        ),
      )
    : 1;
  const barWidth = `${Math.round(fraction * 100)}%`;
  const tone = fraction < 0.1 ? "bg-red-600" : fraction < 0.25 ? "bg-amber-500" : "bg-text";

  return (
    <div className="border-t border-hair px-4 py-2 bg-surface-alt flex items-center gap-3 text-[12px]">
      <span className="font-mono uppercase text-text-muted text-[10.5px]">
        Case #{caseId ?? "—"}
      </span>
      {tier && (
        <span className="font-semibold capitalize">
          {TIER_DISPLAY[tier] ?? tier}
        </span>
      )}
      {showWarning && (
        <div className="flex-1 flex items-center gap-2 min-w-0">
          <div className="flex-1 h-1.5 bg-hair">
            <div
              className={`${tone} h-full transition-[width] duration-200`}
              style={{ width: barWidth }}
            />
          </div>
          <span className="text-text-muted whitespace-nowrap">
            {Math.round(fraction * 100)}% budget left
          </span>
        </div>
      )}
    </div>
  );
}
