"use client";

import { useEffect } from "react";
import Link from "next/link";
import { ABSLogo } from "@/components/abs-logo";
import { Btn } from "@/components/btn";
import { reportError } from "@/lib/error-reporting";

export default function ErrorPage({
  error,
  unstable_retry,
}: {
  error: Error & { digest?: string };
  unstable_retry: () => void;
}) {
  useEffect(() => {
    reportError(error);
  }, [error]);

  return (
    <div className="min-h-screen flex flex-col items-center justify-center bg-surface px-6">
      <div className="max-w-md w-full text-center space-y-6">
        <Link href="/" className="inline-block mb-2" aria-label="ABS home">
          <ABSLogo size={32} />
        </Link>
        <div className="mx-auto w-12 h-[2px] bg-accent" />
        <p className="font-mono text-sm text-text-muted tracking-wider uppercase">
          Something went wrong
        </p>
        <h1 className="text-3xl font-bold text-text tracking-tight">
          Unexpected error
        </h1>
        <p className="text-text-muted text-sm leading-relaxed">
          An error occurred while loading this page. You can try again, or
          return to the home page.
        </p>
        {error.digest && (
          <p className="font-mono text-xs text-text-muted">
            Ref: {error.digest}
          </p>
        )}
        <div className="flex items-center justify-center gap-4 pt-2">
          <Btn onClick={() => unstable_retry()}>Try again</Btn>
          <Link
            href="/"
            className="inline-flex items-center justify-center font-sans font-semibold border-[1.5px] px-[18px] py-[11px] text-[13.5px] tracking-[-0.01em] bg-transparent text-text border-text transition-[transform,opacity] duration-100"
          >
            Home
          </Link>
        </div>
      </div>
    </div>
  );
}
