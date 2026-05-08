// Clerk-backed auth gate. Protects /app/* and /admin/*; everything
// else (marketing, /sign-in, /sign-up, the legacy /access page if a
// future operator re-enables it) is left open.
//
// Why a route matcher rather than `auth.protect()` everywhere:
//   1. Clerk's `auth.protect()` 404s on unauth API requests but
//      redirects on document requests. We want a redirect for the
//      whole product app — the user should land on /sign-in, not
//      see a JSON 404.
//   2. The matcher captures both routes in one place so the rules
//      are auditable without grepping route handlers.
//
// The legacy shared-password flow (cookie `abs_demo` / `abs_admin`,
// route /access) is deliberately left in the tree but no longer
// referenced. Removing the files would force every reviewer to
// chase a rollback through git history; leaving the dead code in
// place lets us flip back by reverting one file if Clerk wiring
// breaks in production.
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
  : // Dev fallback: pass every request through untouched. The chat
    // proxy routes will forward X-Test-User-Id upstream and FastAPI
    // accepts it because no verifier is wired.
    (_req: NextRequest) => NextResponse.next();

export default handler;

export const config = {
  // Skip Next.js internals and static assets; run on everything else
  // including API routes (so route handlers can read auth() too).
  matcher: [
    "/((?!_next|[^?]*\\.(?:html?|css|js(?!on)|jpe?g|webp|png|gif|svg|ttf|woff2?|ico|csv|docx?|xlsx?|zip|webmanifest)).*)",
    "/(api|trpc)(.*)",
  ],
};
