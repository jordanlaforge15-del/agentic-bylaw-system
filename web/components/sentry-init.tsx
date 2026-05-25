"use client";

import * as Sentry from "@sentry/nextjs";
import { useEffect, useRef } from "react";

export function SentryInit() {
  const initialized = useRef(false);

  useEffect(() => {
    if (initialized.current) return;
    const dsn = process.env.NEXT_PUBLIC_SENTRY_DSN;
    if (!dsn) return;

    Sentry.init({
      dsn,
      environment: process.env.NEXT_PUBLIC_SENTRY_ENVIRONMENT ?? "production",
      tracesSampleRate: 0.1,
      sendDefaultPii: false,
    });
    initialized.current = true;
  }, []);

  return null;
}
