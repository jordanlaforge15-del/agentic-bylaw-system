// "Advisory only — not an approval of record" banner.
//
// Per the initiative's liability framing: every page that surfaces
// extracted attributes or evaluator output must show this prominently
// (not in fine print). Reused on submissions/new and submissions/[id].
//
// Static, no state. Pinned at the top of the page below the nav.

export function AdvisoryOnlyBanner() {
  return (
    <div
      data-testid="advisory-only-banner"
      role="note"
      className="mb-6 rounded border-2 border-yellow-500 bg-yellow-50 p-3 text-[14px] text-yellow-900"
    >
      <strong>Advisory only — not an approval of record.</strong> This
      tool surfaces likely bylaw issues for early-stage design review.
      It does not replace a municipal planner&apos;s review or the
      formal development-permit process.
    </div>
  );
}
