// Shared helper for the FastAPI proxy routes. Returns the auth
// header dictionary the proxy should forward upstream, OR `null`
// to signal "the caller is not signed in — return 401".
//
// Two modes, picked by env at request time:
//
//   * CLERK_SECRET_KEY set → Clerk mode. Mint a session JWT via
//     auth().getToken() and send it as Authorization: Bearer <jwt>.
//     The FastAPI side validates against Clerk's JWKS.
//
//   * CLERK_SECRET_KEY unset → dev fallback. Forward the legacy
//     X-Test-User-Id header so the dev backend (uvicorn
//     advisor.api.dev:app) keeps working with no Clerk setup. The
//     FastAPI side accepts this header only when no verifier is
//     wired; never use this mode in production.
//
// In dev-fallback mode, three optional cookies let an e2e test
// override the synthetic identity per browser context:
//   * abs_test_sub_user_id    → X-Test-User-Id header value
//   * abs_test_sub_email      → X-Test-User-Email header (lets the
//                                e2e backend's invite-redemption code
//                                path match an approved InviteRequest
//                                by email on first sign-in)
//   * abs_test_sub_full_name  → X-Test-User-Full-Name header
// Specs set these via context.addCookies() at sign-in and clear them
// at sign-out, simulating the Clerk session-cookie lifecycle without
// hosting Clerk in tests. See web/e2e/auth/fixtures.ts.

import { auth } from "@clerk/nextjs/server";
import { cookies } from "next/headers";

const DEMO_USER_ID = process.env.ADVISOR_DEMO_USER_ID || "demo-user-1";

const TEST_USER_ID_COOKIE = "abs_test_sub_user_id";
const TEST_USER_EMAIL_COOKIE = "abs_test_sub_email";
const TEST_USER_FULL_NAME_COOKIE = "abs_test_sub_full_name";

// True only when the Clerk secret key is set AND looks real.
// We deliberately only check CLERK_SECRET_KEY (no NEXT_PUBLIC_ prefix)
// so the value is resolved at runtime from the container env. The
// publishable key would be inlined at build time by Next.js, which
// caused a production footgun: an image built without the
// `NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY` build-arg ended up with
// `undefined` baked into bundles, making detection return false at
// request time even when Clerk was correctly configured at runtime.
// The example .env file ships placeholders like "sk_test_replace-me";
// if someone copies it without filling in real keys, calling auth()
// would crash @clerk/backend at request time. Falling back to the
// dev path here keeps things working until real keys land.
function isClerkConfigured(): boolean {
  const sk = process.env.CLERK_SECRET_KEY;
  if (!sk) return false;
  if (sk.includes("replace")) return false;
  return /^sk_(test|live)_/.test(sk) && sk.length > 40;
}

export async function buildAdvisorAuthHeaders(): Promise<
  Record<string, string> | null
> {
  if (!isClerkConfigured()) {
    // Read the optional per-test identity cookies. cookies() is async
    // in Next 16; we await it. The cookie store is request-scoped so
    // each browser context sees its own identity even though they
    // share the same dev server process.
    const store = await cookies();
    const subUserId = store.get(TEST_USER_ID_COOKIE)?.value?.trim();
    const subEmail = store.get(TEST_USER_EMAIL_COOKIE)?.value?.trim();
    const subFullName = store
      .get(TEST_USER_FULL_NAME_COOKIE)
      ?.value?.trim();
    const headers: Record<string, string> = {
      "X-Test-User-Id": subUserId && subUserId.length > 0 ? subUserId : DEMO_USER_ID,
    };
    if (subEmail) headers["X-Test-User-Email"] = subEmail;
    if (subFullName) headers["X-Test-User-Full-Name"] = subFullName;
    return headers;
  }
  const { userId, getToken } = await auth();
  if (!userId) {
    return null;
  }
  const token = await getToken();
  if (!token) {
    return null;
  }
  return { Authorization: `Bearer ${token}` };
}
