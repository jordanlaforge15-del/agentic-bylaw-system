import type { Instrumentation } from "next";

export async function register() {
  const dsn = process.env.NEXT_PUBLIC_SENTRY_DSN;
  if (!dsn) return;

  const Sentry = await import("@sentry/nextjs");
  Sentry.init({
    dsn,
    environment: process.env.SENTRY_ENVIRONMENT ?? "production",
    release: process.env.SENTRY_RELEASE,
    tracesSampleRate: parseFloat(
      process.env.SENTRY_TRACES_SAMPLE_RATE ?? "0.1",
    ),
    sendDefaultPii: false,
  });
}

export const onRequestError: Instrumentation.onRequestError = async (
  err,
  request,
  context,
) => {
  if (!process.env.NEXT_PUBLIC_SENTRY_DSN) return;

  const Sentry = await import("@sentry/nextjs");
  Sentry.captureException(err, {
    tags: {
      routerKind: context.routerKind,
      routeType: context.routeType,
    },
    extra: {
      routePath: context.routePath,
      requestPath: request.path,
      requestMethod: request.method,
    },
  });
};
