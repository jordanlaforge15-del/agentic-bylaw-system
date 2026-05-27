// Persistent "research tool — not legal advice" reminder pinned above
// the composer on the main /app chat page. Compact by design: it must
// not compete with the thread or the composer for vertical space.
//
// Does NOT replace the advisory-only-banner (used on submission pages)
// — different context, different copy, same liability intent.

export function ChatDisclaimerBar() {
  return (
    <div
      data-testid="chat-disclaimer-bar"
      role="note"
      className="shrink-0 border-t border-hair px-4 py-1.5 text-center text-[11px] text-text-muted bg-surface-alt"
    >
      Research tool only — not legal advice. Verify all outputs with a
      qualified professional before making planning decisions.
    </div>
  );
}
