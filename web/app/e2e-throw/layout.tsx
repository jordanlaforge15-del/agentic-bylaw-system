// Noindex for the test-only /e2e-throw route (ABS-536).
//
// The page itself is a client component that throws during render, so
// it cannot carry a `metadata` export of its own — metadata is a
// server-side export and a throwing client component never gets to
// contribute one. A layout can, and a layout's <head> is emitted even
// when the page below it goes to the error boundary.
//
// robots.txt disallows /e2e-throw as well. Belt and braces, same
// reasoning as the /submissions layout.

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
