// Demo-grade access gate. Two cookies guard two areas:
//
//   abs_demo  → /app/*       (set by entering DEMO_PASSWORD on /access)
//   abs_admin → /admin/*     (set by entering ADMIN_PASSWORD on /access)
//
// Anything else (marketing pages, API routes, /access itself) is open.
// Missing cookie → 302 to /access?gate=demo|admin&from=<original-path>.
//
// This is intentionally tiny. It's not a real auth system — it's a
// shared-password barrier so the demo URL isn't world-open. When real
// auth lands (Clerk, the advisor backend, etc.), this file goes away.

import { NextRequest, NextResponse } from "next/server";

export function middleware(req: NextRequest) {
  const { pathname } = req.nextUrl;

  if (pathname.startsWith("/admin")) {
    if (req.cookies.get("abs_admin")?.value !== "1") {
      return redirectToAccess(req, "admin", pathname);
    }
  } else if (pathname.startsWith("/app")) {
    if (req.cookies.get("abs_demo")?.value !== "1") {
      return redirectToAccess(req, "demo", pathname);
    }
  }

  return NextResponse.next();
}

function redirectToAccess(
  req: NextRequest,
  gate: "demo" | "admin",
  from: string,
) {
  const url = req.nextUrl.clone();
  url.pathname = "/access";
  url.search = `?gate=${gate}&from=${encodeURIComponent(from)}`;
  return NextResponse.redirect(url);
}

export const config = {
  matcher: ["/app/:path*", "/admin/:path*"],
};
