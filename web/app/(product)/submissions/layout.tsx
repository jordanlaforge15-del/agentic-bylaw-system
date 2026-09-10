// Noindex for the whole /submissions subtree (ABS-536).
//
// Why a layout rather than a `metadata` export on each page: the
// subtree's URL space is open-ended. `/submissions/[id]` answers 200
// for every string — a real id, a bogus uuid, junk — so a crawler can
// walk an unbounded set of near-identical thin pages, and any route
// added under here later inherits the same problem. A layout covers
// the segment and everything below it, including routes that do not
// exist yet; per-page exports only cover what someone remembered.
//
// robots.txt also disallows /submissions (see app/robots.ts). The two
// do different jobs and neither replaces the other: `Disallow` stops
// Googlebot fetching the URL, `noindex` stops it indexing one it
// already knows about. A URL that is only disallowed can still be
// indexed URL-only if something links to it — and Google can never
// see a `noindex` on a page it is forbidden to fetch.
//
// No canonical is declared here. These pages are not indexable, so a
// canonical would be pointing at a URL we have just told Google to
// ignore. The root layout's `canonical: "/"` does leak through as a
// result — harmless while `noindex` stands, and the ABS-536 spec
// asserts the noindex rather than the canonical for exactly that
// reason.

import type { Metadata } from "next";

export const metadata: Metadata = {
  robots: { index: false, follow: false },
};

export default function SubmissionsLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return <>{children}</>;
}
