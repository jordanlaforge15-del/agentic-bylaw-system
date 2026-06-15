// ABS-313 — the on-output disclaimer that rides every priced answer.
//
// Single source of truth so the in-app chat thread (chat-thread.tsx)
// and the post-purchase answer view (answer-view.tsx) render the exact
// same liability language. A priced answer is never shown without it —
// if you add a third answer surface, render this, do not re-type the
// copy. The data-testid is asserted by the ABS-313 / ABS-321 e2e specs.

export function PaidAnswerDisclaimer() {
  return (
    <div
      data-testid="paid-answer-disclaimer"
      role="note"
      className="font-mono text-text-muted"
      style={{ fontSize: 10, letterSpacing: "0.04em" }}
    >
      Bylaw-analysis output — not an official municipal determination.
      Verify before relying.
    </div>
  );
}
