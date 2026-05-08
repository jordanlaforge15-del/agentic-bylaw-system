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

import { auth } from "@clerk/nextjs/server";

const DEMO_USER_ID = process.env.ADVISOR_DEMO_USER_ID || "demo-user-1";

// True only when both Clerk keys are set AND look like real values.
// The example .env file ships placeholders like "sk_test_replace-me";
// if someone copies it without filling in real keys, calling auth()
// would crash @clerk/backend at request time. Falling back to the
// dev path here keeps things working until real keys land.
function isClerkConfigured(): boolean {
  const sk = process.env.CLERK_SECRET_KEY;
  const pk = process.env.NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY;
  if (!sk || !pk) return false;
  if (sk.includes("replace") || pk.includes("replace")) return false;
  return /^sk_(test|live)_/.test(sk) && /^pk_(test|live)_/.test(pk);
}

export async function buildAdvisorAuthHeaders(): Promise<
  Record<string, string> | null
> {
  if (!isClerkConfigured()) {
    return { "X-Test-User-Id": DEMO_USER_ID };
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
