// Canonical + social-card metadata for public pages (ABS-510).
//
// Why a helper instead of hand-writing the blocks on each page: Next
// merges `metadata` exports *shallowly*, one key at a time. A page that
// sets its own `openGraph` replaces the root layout's block wholesale
// rather than extending it — so every page needs a complete block, and
// hand-copying nine complete blocks is how the og:url on /pricing ends
// up still saying "/". One function, one shape, no drift.
//
// The same shallow-merge rule is why `alternates` matters here more than
// it looks. The root layout declares `canonical: "/"` (correct for the
// home page). Any page that does NOT declare its own canonical inherits
// that one and tells Google it is a duplicate of the home page. Every
// indexable route must therefore call this helper.
//
// All URLs are passed as root-relative paths and resolved against
// `metadataBase` (set in app/layout.tsx from SITE_URL) at render time.
// That keeps the absolute hostname in exactly one place — web/lib/
// site-url.ts — shared with robots.txt and sitemap.xml.

import type { Metadata } from "next";

/** Brand name as it should appear in a link preview's site label. */
export const SITE_NAME = "ABS° — Agentic Bylaw System";

/**
 * Static 1200x630 social card under web/public/. A file, not a
 * `next/og` route: nothing to keep warm, nothing to rate-limit, and it
 * is served by the same static handler as the favicon.
 * Regenerate with `node scripts/generate-og-image.mjs` from web/.
 */
export const OG_IMAGE_PATH = "/og-image.png";
export const OG_IMAGE_WIDTH = 1200;
export const OG_IMAGE_HEIGHT = 630;
const OG_IMAGE_ALT =
  "ABS° — Agentic Bylaw System. Halifax Regional Centre Land Use By-law, cited to the clause.";

/** The image entry shared by the Open Graph and Twitter blocks. */
export const OG_IMAGE = {
  url: OG_IMAGE_PATH,
  width: OG_IMAGE_WIDTH,
  height: OG_IMAGE_HEIGHT,
  alt: OG_IMAGE_ALT,
} as const;

export type PageMetadataInput = {
  /** Root-relative path of the page, e.g. "/pricing". "/" for home. */
  path: string;
  title: string;
  description: string;
  /** Set for account/gated pages that should stay out of the index. */
  noindex?: boolean;
};

/**
 * Build a page's full metadata: title, description, self-referencing
 * canonical, and complete Open Graph / Twitter card blocks.
 */
export function pageMetadata({
  path,
  title,
  description,
  noindex = false,
}: PageMetadataInput): Metadata {
  return {
    title,
    description,
    alternates: { canonical: path },
    openGraph: {
      type: "website",
      siteName: SITE_NAME,
      title,
      description,
      url: path,
      locale: "en_CA",
      images: [OG_IMAGE],
    },
    twitter: {
      card: "summary_large_image",
      title,
      description,
      images: [OG_IMAGE],
    },
    ...(noindex ? { robots: { index: false, follow: false } } : {}),
  };
}
