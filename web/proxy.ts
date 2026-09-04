// Auth gate. Protects /app/* and /admin/*; everything else
// (marketing, /sign-in, /sign-up) is left open.
//
// Two modes, picked at request time by isClerkConfigured():
//
//   * Clerk configured → clerkMiddleware enforces auth on protected
//     routes. Unauth requests redirect to /sign-in. This is the only
//     mode production ever runs in.
//
//   * Clerk NOT configured → local-dev convenience: protected routes
//     are open so `npm run dev` works without Clerk keys, but ONLY
//     when NODE_ENV is not "production". A production build with no
//     Clerk secret is a misconfiguration, not a mode, so it fails
//     closed with a 503 rather than serving the app unauthenticated.
//     (ABS-530 removed the shared-password fallback that used to sit
//     here — a single password handed to friends, no per-user
//     identity, no audit trail.)
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

const isProtectedRoute = createRouteMatcher([
  "/app(.*)",
  "/admin(.*)",
  "/cases/new",
]);
const isAdminRoute = createRouteMatcher(["/admin(.*)"]);

// Comma-separated list of Clerk userIds (e.g. "user_2abc,user_2def")
// allowed into /admin/*. Read once at module load; restart the
// container to add an admin. Empty list = nobody is admin (fail
// closed).
const ADMIN_USER_IDS: ReadonlySet<string> = new Set(
  (process.env.ADVISOR_ADMIN_CLERK_USER_IDS || "")
    .split(",")
    .map((s) => s.trim())
    .filter(Boolean),
);

// True only when the Clerk secret key is set AND looks real.
// We deliberately read CLERK_SECRET_KEY (no NEXT_PUBLIC_ prefix) so
// the value is resolved at runtime from the container env. The
// publishable key would be inlined at build time by Next.js, which
// caused a production footgun: an image built without
// `--build-arg NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY=...` ended up with
// `undefined` baked into the bundled proxy, making this function
// return false at request time and falling through to the legacy
// cookie gate even though Clerk was correctly configured at runtime.
// CLERK_SECRET_KEY is server-only so Next never inlines it.
//
// The example file ships placeholders like "sk_test_replace-me" — if
// someone copies the example without filling in real keys, Clerk's
// backend rejects them at request time. Detecting the placeholder
// shape here lets us fall back to the dev path cleanly instead of
// crashing every page render. Real Clerk keys are >40 chars.
function isClerkConfigured(): boolean {
  const k = process.env.CLERK_SECRET_KEY;
  if (!k) return false;
  if (k.includes("replace")) return false;
  return /^sk_(test|live)_/.test(k) && k.length > 40;
}

const handler = isClerkConfigured()
  ? clerkMiddleware(async (auth, req) => {
      if (!isProtectedRoute(req)) return;
      // All protected routes require an authenticated Clerk session.
      // auth.protect() redirects to /sign-in for HTML requests and
      // returns 404 for API requests — exactly what we want here.
      await auth.protect();
      // /admin/* additionally requires the signed-in user to be on
      // the operator allowlist. We re-check userId AFTER protect()
      // so we know the session is valid. Non-admins get a 404 — same
      // shape an unprotected URL miss would have, so this doesn't
      // leak the existence of /admin to random signed-in users.
      if (isAdminRoute(req)) {
        const { userId } = await auth();
        if (!userId || !ADMIN_USER_IDS.has(userId)) {
          return new NextResponse("Not found", { status: 404 });
        }
      }
    })
  : // Clerk-not-configured path. There is no second auth scheme to fall
    // back to any more, so this splits on where we're running:
    //
    //   * dev (`npm run dev` with no Clerk keys) — protected routes are
    //     open. advisor-auth.ts already forwards a synthetic
    //     X-Test-User-Id in this mode, so the app is usable; the dev
    //     server is on localhost and has no real users to protect.
    //
    //   * production build — this is a misconfigured deploy. Serving
    //     /app and /admin unauthenticated would be worse than being
    //     down, so protected routes 503 until Clerk is wired.
    (req: NextRequest) => {
      if (!isProtectedRoute(req)) return NextResponse.next();
      if (process.env.NODE_ENV === "production") {
        return new NextResponse(
          "Authentication is not configured on this deployment.",
          { status: 503 },
        );
      }
      return NextResponse.next();
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
