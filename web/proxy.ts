// Auth gate. Protects /app/* and /admin/*; everything else
// (marketing, /sign-in, /sign-up, /access) is left open.
//
// Two modes, picked at request time by isClerkConfigured():
//
//   * Clerk configured → clerkMiddleware enforces auth on protected
//     routes. Unauth requests redirect to /sign-in.
//
//   * Clerk NOT configured → falls back to the legacy shared-password
//     gate (cookie abs_demo / abs_admin, /access page, /api/access
//     route). This is the "trial deployment" mode: a single shared
//     password per gate, handed out to friends. Set DEMO_PASSWORD
//     (and optionally ADMIN_PASSWORD) on the web container. Once
//     real Clerk keys are wired the fallback becomes unreachable
//     automatically — no code change to flip back.
//
// Why a route matcher rather than `auth.protect()` everywhere:
//   1. Clerk's `auth.protect()` 404s on unauth API requests but
//      redirects on document requests. We want a redirect for the
//      whole product app — the user should land on /sign-in, not
//      see a JSON 404.
//   2. The matcher captures both routes in one place so the rules
//      are auditable without grepping route handlers.
//
// File-name note: Next.js 16 renamed the `middleware.ts` convention
// to `proxy.ts`. The Clerk SDK helper is still called
// `clerkMiddleware` because it predates the rename — the import is
// correct, only the host file is named per the new convention.

import type { NextRequest } from "next/server";
import { NextResponse } from "next/server";
import { clerkMiddleware, createRouteMatcher } from "@clerk/nextjs/server";

const isProtectedRoute = createRouteMatcher(["/app(.*)", "/admin(.*)"]);

// True only when the Clerk publishable key is set AND looks real.
// The example file ships placeholders like "pk_test_replace-me" — if
// someone copies the example without filling in real keys, Clerk's
// backend rejects them at request time with "Missing publishableKey".
// Detecting the placeholder shape here lets us fall back to the dev
// path cleanly instead of crashing every page render. Real Clerk keys
// are >40 chars; placeholders are short.
function isClerkConfigured(): boolean {
  const k = process.env.NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY;
  if (!k) return false;
  if (k.includes("replace")) return false;
  return /^pk_(test|live)_/.test(k) && k.length > 40;
}

const handler = isClerkConfigured()
  ? clerkMiddleware(async (auth, req) => {
      if (isProtectedRoute(req)) {
        await auth.protect();
      }
    })
  : // Clerk-not-configured fallback: reuse the legacy shared-password
    // gate. /app/* requires the abs_demo cookie, /admin/* requires
    // abs_admin. Missing cookie redirects to /access with the right
    // gate query param. Set DEMO_PASSWORD (and optionally
    // ADMIN_PASSWORD) env vars for /api/access to validate against.
    // Chat proxy routes still send X-Test-User-Id upstream.
    (req: NextRequest) => {
      if (!isProtectedRoute(req)) return NextResponse.next();
      const path = req.nextUrl.pathname;
      const isAdminRoute = path.startsWith("/admin");
      const cookieName = isAdminRoute ? "abs_admin" : "abs_demo";
      if (req.cookies.get(cookieName)?.value === "1") {
        return NextResponse.next();
      }
      const url = new URL("/access", req.url);
      url.searchParams.set("from", path);
      if (isAdminRoute) url.searchParams.set("gate", "admin");
      return NextResponse.redirect(url);
    };

export default handler;

export const config = {
  // Skip Next.js internals and static assets; run on everything else
  // including API routes (so route handlers can read auth() too).
  matcher: [
    "/((?!_next|[^?]*\\.(?:html?|css|js(?!on)|jpe?g|webp|png|gif|svg|ttf|woff2?|ico|csv|docx?|xlsx?|zip|webmanifest)).*)",
    "/(api|trpc)(.*)",
  ],
};
