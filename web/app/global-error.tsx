"use client";

import { useEffect } from "react";
import { reportError } from "@/lib/error-reporting";

export default function GlobalError({
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
    <html lang="en">
      <body
        style={{
          margin: 0,
          fontFamily: '"Inter Tight", system-ui, sans-serif',
          background: "#ffffff",
          color: "#0a0a0a",
          display: "flex",
          alignItems: "center",
          justifyContent: "center",
          minHeight: "100vh",
          padding: "24px",
        }}
      >
        <div style={{ maxWidth: 420, textAlign: "center" }}>
          {/* ABS wordmark inline — no Tailwind available at this error level */}
          <div
            style={{
              display: "inline-flex",
              alignItems: "center",
              fontFamily: '"Inter Tight", system-ui, sans-serif',
              fontWeight: 800,
              fontSize: 32,
              letterSpacing: "-0.06em",
              marginBottom: 20,
            }}
          >
            <span>abs</span>
            <span
              style={{
                display: "inline-block",
                width: 32 * 0.18,
                height: 32 * 0.78,
                marginLeft: 32 * 0.08,
                background: "#c9f24c",
              }}
            />
          </div>
          <div
            style={{
              width: 48,
              height: 2,
              background: "#c9f24c",
              margin: "0 auto 20px",
            }}
          />
          <p
            style={{
              fontFamily: '"JetBrains Mono", monospace',
              fontSize: 13,
              color: "#7a7468",
              letterSpacing: "0.05em",
              textTransform: "uppercase",
              marginBottom: 16,
            }}
          >
            Something went wrong
          </p>
          <h1
            style={{
              fontSize: 28,
              fontWeight: 700,
              letterSpacing: "-0.02em",
              margin: "0 0 12px",
            }}
          >
            Unexpected error
          </h1>
          <p style={{ fontSize: 14, color: "#7a7468", lineHeight: 1.6 }}>
            A critical error occurred. Please try again.
          </p>
          <button
            onClick={() => unstable_retry()}
            style={{
              marginTop: 24,
              padding: "11px 18px",
              fontSize: 13.5,
              fontWeight: 600,
              fontFamily: '"Inter Tight", system-ui, sans-serif',
              cursor: "pointer",
              border: "1.5px solid #0a0a0a",
              background: "#0a0a0a",
              color: "#ffffff",
            }}
          >
            Try again
          </button>
        </div>
      </body>
    </html>
  );
}
