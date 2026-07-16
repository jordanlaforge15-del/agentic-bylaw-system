import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  output: "standalone",
  distDir: process.env.NEXT_DIST_DIR || ".next",
  // Next.js's standalone tracer walks static `require`/`import` chains
  // to decide which node_modules to bundle. nodemailer pulls its
  // transports via `require(name)` from a dynamic string, which the
  // tracer can't follow — so without this hint, `node_modules/nodemailer`
  // is missing from the standalone output and `/api/admin/invites/[id]/approve`
  // crashes with MODULE_NOT_FOUND at first send. Explicit include.
  outputFileTracingIncludes: {
    "/api/admin/invites/**": ["./node_modules/nodemailer/**/*"],
  },
  // Generate source maps in production so Sentry can symbolicate
  // stack traces. The maps are served alongside the JS files but are
  // only fetched when a browser's devtools are open — no performance
  // cost for regular users.
  productionBrowserSourceMaps: true,

  // ABS-19: when the e2e Clerk mock is active, swap @clerk/nextjs/server
  // with our lightweight mock that reads test cookies instead of calling
  // Clerk's real backend. This exercises the clerkMiddleware branch in
  // proxy.ts and the auth().getToken() branch in advisor-auth.ts with
  // zero dependency on a live Clerk account. The alias is inert in
  // production (E2E_CLERK_MOCK is never set outside the test stack).
  ...(process.env.E2E_CLERK_MOCK === "1" && {
    turbopack: {
      resolveAlias: {
        "@clerk/nextjs/server": "./lib/clerk-test-mock",
      },
    },
  }),
};

export default nextConfig;
