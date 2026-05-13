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
  : // Clerk-not-configured fallback: reuse the legacy shared-password
    // gate for local dev. Production must run Clerk-on; the fallback
    // is here so `npm run dev` against the dev backend keeps working
    // without Clerk keys. /admin/* uses the abs_admin cookie path
    // here too — there's no Clerk-based admin check without Clerk.
    (req: NextRequest) => {
      if (!isProtectedRoute(req)) return NextResponse.next();
      const path = req.nextUrl.pathname;
      const isAdmin = path.startsWith("/admin");
      const cookieName = isAdmin ? "abs_admin" : "abs_demo";
      if (req.cookies.get(cookieName)?.value === "1") {
        return NextResponse.next();
      }
      const url = new URL("/access", req.url);
      url.searchParams.set("from", path);
      if (isAdmin) url.searchParams.set("gate", "admin");
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
