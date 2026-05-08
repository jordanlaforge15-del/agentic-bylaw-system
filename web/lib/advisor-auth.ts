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

export async function buildAdvisorAuthHeaders(): Promise<
  Record<string, string> | null
> {
  if (!process.env.CLERK_SECRET_KEY) {
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
