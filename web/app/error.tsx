"use client";

import { useEffect } from "react";
import Link from "next/link";
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
    <div className="min-h-screen flex items-center justify-center bg-surface px-6">
      <div className="max-w-md w-full text-center space-y-6">
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
          <button
            onClick={() => unstable_retry()}
            className="inline-flex items-center justify-center cursor-pointer font-sans font-semibold border-[1.5px] px-[18px] py-[11px] text-[13.5px] tracking-[-0.01em] bg-text text-surface border-text transition-[transform,opacity] duration-100"
          >
            Try again
          </button>
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
