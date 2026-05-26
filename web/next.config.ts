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
};

export default nextConfig;
