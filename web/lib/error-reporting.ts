// Centralized error reporting. Currently logs to console; designed to
// be wired to Sentry (or another service) when that integration lands.
// All error boundaries funnel through here so the wiring is one-line.

export function reportError(error: Error & { digest?: string }): void {
  // When Sentry is integrated, replace this body with:
  //   Sentry.captureException(error);
  console.error("[ABS error-reporting]", error);
}
