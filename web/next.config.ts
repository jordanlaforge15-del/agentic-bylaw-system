import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  output: "standalone",
  // Next.js's standalone tracer walks static `require`/`import` chains
  // to decide which node_modules to bundle. nodemailer pulls its
  // transports via `require(name)` from a dynamic string, which the
  // tracer can't follow — so without this hint, `node_modules/nodemailer`
  // is missing from the standalone output and `/api/admin/invites/[id]/approve`
  // crashes with MODULE_NOT_FOUND at first send. Explicit include.
  outputFileTracingIncludes: {
    "/api/admin/invites/**": ["./node_modules/nodemailer/**/*"],
  },
};

export default nextConfig;
