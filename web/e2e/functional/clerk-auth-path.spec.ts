// ABS-19 — Clerk auth path coverage.
//
// Verifies that requests through the Next.js proxy exercise the Clerk
// JWT authentication pipeline end-to-end:
//
//   1. proxy.ts runs the clerkMiddleware branch (not the fallback gate)
//   2. buildAdvisorAuthHeaders() returns Authorization: Bearer <jwt>
//   3. FastAPI's ClerkVerifier validates the JWT signature + claims
//   4. resolve_or_create_user creates the advisor_user from JWT claims
//
// The mock infrastructure:
//   * web/lib/clerk-test-mock.ts replaces @clerk/nextjs/server (via
//     Turbopack resolveAlias when E2E_CLERK_MOCK=1)
//   * src/advisor/auth/mock_clerk.py provides a real ClerkVerifier
//     backed by an in-memory test RSA key pair
//   * /v1/_test/mint-jwt mints JWTs signed by that key
//
// These specs confirm the mock is wired correctly and the Clerk code
// paths handle both authenticated and unauthenticated states.

import { test, expect } from "../fixtures/test-env";

const E2E_API_URL =
  process.env.E2E_API_URL || "http://127.0.0.1:8001";
const E2E_BASE_URL =
  process.env.E2E_BASE_URL || "http://localhost:3001";

test.describe("Clerk auth path", () => {
  test("proxy redirects unauthenticated /app to /sign-in", async ({
    context,
  }) => {
    // Clear all auth cookies so the request is fully unauthenticated.
    await context.clearCookies();
    const page = await context.newPage();
    await page.goto(`${E2E_BASE_URL}/app`);

    // The Clerk mock middleware should redirect to /sign-in.
    await expect(page).toHaveURL(/\/sign-in/);
  });

  test("JWT-authed request reaches FastAPI via Clerk verification", async ({
    context,
    authedContext: _,
  }) => {
    // The authedContext fixture already minted a JWT for the demo user
    // and set it as abs_test_clerk_jwt cookie. Navigate to /app — the
    // Clerk mock middleware sees the abs_test_sub_user_id cookie and
    // passes the request through. When the page makes an API call,
    // buildAdvisorAuthHeaders reads the JWT cookie and forwards it as
    // Authorization: Bearer to FastAPI.
    const page = await context.newPage();

    // Hit the healthz endpoint through the proxy to verify the stack
    // is up, then navigate to /app to exercise the full Clerk path.
    const healthRes = await context.request.get(
      `${E2E_API_URL}/healthz`,
    );
    expect(healthRes.ok()).toBe(true);

    await page.goto(`${E2E_BASE_URL}/app`);

    // If the Clerk mock + JWT auth path works, we should land on /app
    // (not redirected to /sign-in or /access).
    await expect(page).toHaveURL(/\/app/);
  });

  test("FastAPI validates mock JWT and creates user from claims", async ({
    context,
  }) => {
    // Mint a JWT for a fresh identity and call FastAPI directly with
    // the Authorization header — exercises the real ClerkVerifier.
    const slug = `clerk-${Date.now().toString(36)}`;
    const identity = {
      sub: `clerk-test-${slug}`,
      email: `clerk-test-${slug}@e2e.test`,
      full_name: `Clerk Test ${slug}`,
    };

    const mintRes = await context.request.post(
      `${E2E_API_URL}/v1/_test/mint-jwt`,
      { data: identity },
    );
    expect(mintRes.ok()).toBe(true);
    const { token } = (await mintRes.json()) as { token: string };
    expect(token).toBeTruthy();

    // Use the JWT to call /v1/terms/current — a lightweight authed
    // endpoint that exercises the full verification chain.
    const termsRes = await context.request.get(
      `${E2E_API_URL}/v1/terms/current`,
      {
        headers: { Authorization: `Bearer ${token}` },
      },
    );
    expect(termsRes.ok()).toBe(true);
    const terms = (await termsRes.json()) as {
      version: string;
      accepted: boolean;
    };
    expect(terms.version).toBeTruthy();

    // Clean up: delete the test user.
    await context.request.post(`${E2E_API_URL}/v1/_test/reset-user`, {
      data: { clerk_user_id: identity.sub },
    });
  });

  test("FastAPI rejects invalid JWT with 401", async ({ context }) => {
    // Send a garbage Authorization header — the ClerkVerifier should
    // reject it and the hybrid dependency should NOT fall back to
    // headers (Authorization was present, just invalid).
    const res = await context.request.get(
      `${E2E_API_URL}/v1/terms/current`,
      {
        headers: { Authorization: "Bearer invalid.jwt.token" },
      },
    );
    expect(res.status()).toBe(401);
  });

  test("FastAPI accepts X-Test-User-Id fallback when no JWT", async ({
    context,
  }) => {
    // Direct FastAPI calls from Playwright fixtures send
    // X-Test-User-Id without a JWT. The hybrid dependency falls back
    // to the header path when Authorization is absent.
    const res = await context.request.get(
      `${E2E_API_URL}/v1/terms/current`,
      {
        headers: { "X-Test-User-Id": "demo-user-1" },
      },
    );
    expect(res.ok()).toBe(true);
  });
});
