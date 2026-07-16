"use client";

// Test-only page that throws during render so Playwright can verify
// that the root error boundary catches it and renders the fallback UI.
// Only available when NODE_ENV !== "production".

export default function TestThrowPage() {
  if (process.env.NODE_ENV === "production") {
    return <p>Not available</p>;
  }
  throw new Error("Deliberate test error for error boundary verification");
}
