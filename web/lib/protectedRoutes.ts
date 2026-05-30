// Single source of truth for which client-side routes are "authorized"
// pages — i.e. the product surface that is only viewable when logged in.
// Consumed by the global feedback widget (GeneralFeedback.tsx) to decide
// where its launcher is shown. Auth itself is enforced server-side per
// route (there is no Next.js middleware in this app), so this list only
// governs UI placement, not access control.
//
// These mirror the `(product)` route group: /app (the chat shell, billing,
// terms, …) and /submissions (BIM upload + review).
export const PROTECTED_PREFIXES = ["/app", "/submissions"] as const;

/**
 * True when `pathname` is an authenticated-only product page where the
 * global feedback launcher should appear.
 *
 * Print views (`/app/print`, …) render a chrome-free, paper-oriented page,
 * so the floating widget is suppressed there.
 */
export function isProtectedPath(pathname: string): boolean {
  if (pathname.includes("/print")) return false;
  return PROTECTED_PREFIXES.some(
    (prefix) => pathname === prefix || pathname.startsWith(`${prefix}/`),
  );
}
