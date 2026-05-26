// Mock @clerk/nextjs/server for e2e tests (ABS-19).
//
// Active only when E2E_CLERK_MOCK=1 AND CLERK_SECRET_KEY is set to the
// test key — the Turbopack resolveAlias in next.config.ts swaps
// "@clerk/nextjs/server" to this file.
//
// How it works:
//
//   * clerkMiddleware reads the abs_test_sub_user_id cookie to decide
//     whether the request is "authenticated". Unauthenticated requests
//     to protected routes redirect to /sign-in, matching real Clerk.
//
//   * auth() (used by advisor-auth.ts) reads the same cookie for
//     userId and abs_test_clerk_jwt for the Bearer token. The JWT is
//     minted by the /v1/_test/mint-jwt FastAPI endpoint and set as a
//     cookie by the Playwright signInAs fixture.
//
//   * createRouteMatcher is a simple regex builder — Clerk's patterns
//     ("/app(.*)", "/admin(.*)") are already valid regex fragments.

import type { NextRequest } from "next/server";
import { NextResponse } from "next/server";
import { cookies } from "next/headers";

// ---------- createRouteMatcher ----------

type RouteMatcher = (req: NextRequest) => boolean;

export function createRouteMatcher(patterns: string[]): RouteMatcher {
  const regexes = patterns.map((p) => new RegExp("^" + p + "$"));
  return (req: NextRequest) =>
    regexes.some((r) => r.test(req.nextUrl.pathname));
}

// ---------- clerkMiddleware ----------

type MockAuthObject = {
  userId: string | null;
};

type MockAuthFn = {
  (): Promise<MockAuthObject>;
  protect(): Promise<void>;
};

type MiddlewareHandler = (
  auth: MockAuthFn,
  req: NextRequest,
) => Promise<NextResponse | void>;

const UNAUTHENTICATED_SENTINEL = "__clerk_mock_unauthenticated__";

export function clerkMiddleware(handler: MiddlewareHandler) {
  return async (req: NextRequest): Promise<NextResponse> => {
    const userId =
      req.cookies.get("abs_test_sub_user_id")?.value?.trim() || null;

    const authFn: MockAuthFn = Object.assign(
      async (): Promise<MockAuthObject> => ({ userId }),
      {
        protect: async (): Promise<void> => {
          if (!userId) {
            throw new Error(UNAUTHENTICATED_SENTINEL);
          }
        },
      },
    );

    try {
      const result = await handler(authFn, req);
      if (result) return result;
    } catch (e: unknown) {
      if (e instanceof Error && e.message === UNAUTHENTICATED_SENTINEL) {
        return NextResponse.redirect(new URL("/sign-in", req.url));
      }
      throw e;
    }

    return NextResponse.next();
  };
}

// ---------- auth (standalone, for server components / API routes) ----------

type StandaloneAuthObject = {
  userId: string | null;
  sessionId?: string;
  getToken: () => Promise<string | null>;
};

export async function auth(): Promise<StandaloneAuthObject> {
  const store = await cookies();
  const userId = store.get("abs_test_sub_user_id")?.value?.trim() || null;
  const jwt = store.get("abs_test_clerk_jwt")?.value || null;

  return {
    userId,
    sessionId: userId ? "sess_e2e_mock" : undefined,
    getToken: async () => jwt,
  };
}
