// Centralized error reporting. Funnels all error boundaries to Sentry
// when NEXT_PUBLIC_SENTRY_DSN is set; no-ops silently otherwise.

export function reportError(error: Error & { digest?: string }): void {
  if (!process.env.NEXT_PUBLIC_SENTRY_DSN) return;

  import("@sentry/nextjs")
    .then((Sentry) => Sentry.captureException(error))
    .catch(() => {});
}
