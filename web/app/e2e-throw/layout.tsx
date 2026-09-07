// Noindex for the test-only /e2e-throw route (ABS-536).
//
// The page itself is a client component that throws during render, so
// it cannot carry a `metadata` export of its own — metadata is a
// server-side export and a throwing client component never gets to
// contribute one. A layout can, and a layout's <head> is emitted even
// when the page below it goes to the error boundary.
//
// robots.txt disallows /e2e-throw as well. Belt and braces, same
// reasoning as the /submissions layout — and here the Disallow is the
// load-bearing half, because this `noindex` is the one thing in
// ABS-536 the e2e suite cannot verify. The stack runs `next dev`,
// where the page throws by design so the error-boundary spec has
// something to catch; the meta tag a test sees on this route is Next's
// own error-page one, not necessarily this layout's. The layout only
// takes effect in production, where the page renders "Not available".
// abs536-no-unintended-indexable-routes.spec.ts therefore asserts the
// robots.txt entry for this route and not the tag.

import type { Metadata } from "next";

export const metadata: Metadata = {
  robots: { index: false, follow: false },
};

export default function E2eThrowLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return <>{children}</>;
}
