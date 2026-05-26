// Centralized error reporting. All error boundaries funnel through here.
// Dynamically imports Sentry so the SDK is only loaded when DSN is set.

export async function reportError(error: Error & { digest?: string }): Promise<void> {
  console.error("[ABS error-reporting]", error);
  const dsn = process.env.NEXT_PUBLIC_SENTRY_DSN;
  if (!dsn) return;
  const Sentry = await import("@sentry/nextjs");
  Sentry.captureException(error);
}
