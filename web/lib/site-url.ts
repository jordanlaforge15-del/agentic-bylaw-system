// Single source of truth for the site's absolute public origin.
//
// Anything that has to emit a fully-qualified URL — robots.txt's
// `Sitemap:` line, sitemap.xml's `<loc>` values, and the root layout's
// `metadataBase` (ABS-510), which in turn resolves every canonical,
// og:url and og:image on the site — reads it from here rather than
// hardcoding the hostname in each spot. One env var, one normalisation
// rule, no drift.
//
// Env:
//   SITE_URL   absolute origin, e.g. "https://agenticbylawsystems.com".
//              Trailing slash optional — it is stripped.
//
// Deliberately NOT `NEXT_PUBLIC_SITE_URL`: Next.js inlines
// NEXT_PUBLIC_* at build time, which already bit this codebase once
// (see the CLERK_SECRET_KEY note in proxy.ts) — an image built without
// the build-arg bakes `undefined` into the bundle and the runtime env
// can never fix it. Every consumer of this value renders on the
// server, so a plain server-only var resolved at request time is both
// safer and simpler to operate.

const FALLBACK_SITE_URL = "https://agenticbylawsystems.com";

/**
 * The site origin with no trailing slash, e.g. `https://example.com`.
 * Falls back to the production hostname when SITE_URL is unset or
 * unparseable, so a misconfigured deploy still emits valid absolute
 * URLs instead of `undefined/pricing`.
 */
export function siteUrl(): string {
  const raw = process.env.SITE_URL?.trim();
  if (!raw) return FALLBACK_SITE_URL;
  try {
    // new URL() validates the scheme+host and normalises the origin;
    // it throws on bare hostnames like "example.com".
    return new URL(raw).origin;
  } catch {
    return FALLBACK_SITE_URL;
  }
}

/**
 * Join a root-relative path onto the site origin.
 * `absoluteUrl("/")` → `https://example.com`
 * `absoluteUrl("/pricing")` → `https://example.com/pricing`
 */
export function absoluteUrl(path: string): string {
  const base = siteUrl();
  if (!path || path === "/") return base;
  return `${base}${path.startsWith("/") ? path : `/${path}`}`;
}
