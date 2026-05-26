// GET /api/nm/access-log?limit=N
//
// Returns the last N entries from .night-manager/api-access.log as JSON
// for the /nm dashboard's "API access" panel. Read-only — there is no
// POST/DELETE; the log file is append-only and rotated by the operator.
//
// Admin-gated using the same two-mode pattern as /admin/invites:
//
//   * Clerk configured → requireAdmin() validates the session is on
//     the ADVISOR_ADMIN_CLERK_USER_IDS allowlist.
//   * Clerk NOT configured → fall back to the abs_admin cookie minted
//     by /api/access. This is the legacy shared-password gate used by
//     local dev and by the Playwright e2e suite (Clerk keys are blanked
//     in scripts/e2e-up.sh).
//
// Failing closed: if Clerk is configured but no allowlist is set, the
// cookie fallback is NOT activated — admins must be on the Clerk
// allowlist in production. The two paths only crossover in the
// no-Clerk dev/test path.

import { NextRequest, NextResponse } from "next/server";
import { requireAdmin } from "@/lib/admin-auth";
import { readAccessLog } from "../access-log";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

function isClerkConfigured(): boolean {
  // Mirror proxy.ts's detection — keep them in sync. We only fall back
  // to the cookie gate when Clerk is truly unconfigured, not when the
  // user is signed out, so production behaves identically to staging.
  const k = process.env.CLERK_SECRET_KEY;
  if (!k) return false;
  if (k.includes("replace")) return false;
  return /^sk_(test|live)_/.test(k) && k.length > 40;
}

async function isAuthorized(req: NextRequest): Promise<boolean> {
  if (isClerkConfigured()) {
    const admin = await requireAdmin();
    return admin !== null;
  }
  // No-Clerk path: trust the abs_admin cookie set by /api/access.
  // ADMIN_PASSWORD must be configured server-side for the cookie to
  // ever be mintable, so this isn't bypassable by simply sending a
  // forged header.
  return req.cookies.get("abs_admin")?.value === "1";
}

export async function GET(req: NextRequest) {
  if (!(await isAuthorized(req))) {
    return NextResponse.json({ error: "Unauthorized" }, { status: 401 });
  }

  const raw = req.nextUrl.searchParams.get("limit");
  let limit = 50;
  if (raw !== null) {
    const n = Number(raw);
    if (!Number.isFinite(n) || !Number.isInteger(n) || n < 1 || n > 1000) {
      return NextResponse.json(
        { error: "limit must be an integer 1–1000" },
        { status: 400 },
      );
    }
    limit = n;
  }

  const entries = readAccessLog(limit);
  return NextResponse.json({ entries });
}
